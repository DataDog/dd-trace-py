import inspect
import types
from typing import Any
from typing import Optional
from typing import Union
from typing import get_args
from typing import get_origin

from ddtrace.internal import core
from ddtrace.internal.constants import COMPONENT
from ddtrace.internal.utils import get_argument_value
from ddtrace.llmobs._constants import DISPATCH_ON_TOOL_CALL
from ddtrace.llmobs._integrations.agent_manifest import as_str
from ddtrace.llmobs._integrations.agent_manifest import build_agent_manifest
from ddtrace.llmobs._integrations.agent_manifest import callable_name
from ddtrace.llmobs._integrations.agent_manifest import filter_model_settings
from ddtrace.llmobs._integrations.agent_manifest import instruction_fields
from ddtrace.llmobs._integrations.agent_manifest import is_number
from ddtrace.llmobs._integrations.agent_manifest import normalize_tool
from ddtrace.llmobs._integrations.agent_manifest import type_name
from ddtrace.llmobs._integrations.base import BaseLLMIntegration
from ddtrace.llmobs._integrations.google_utils import extract_message_from_part_google_genai
from ddtrace.llmobs._integrations.google_utils import extract_messages_from_adk_events
from ddtrace.llmobs._utils import _annotate_llmobs_span_data
from ddtrace.llmobs._utils import safe_json
from ddtrace.llmobs.types import AgentCapability
from ddtrace.llmobs.types import AgentManifest
from ddtrace.trace import Span


class GoogleAdkIntegration(BaseLLMIntegration):
    _integration_name = "google_adk"

    # Maps the traced operation to its LLMObs span kind. Code execution is a call to a program,
    # which maps to the "tool" span kind ("code_execute" is not a valid LLMObs span kind).
    _OPERATION_TO_SPAN_KIND = {"agent": "agent", "tool": "tool", "code_execute": "tool"}

    def _set_base_span_tags(
        self, span: Span, model: Optional[Any] = None, provider: Optional[Any] = None, **kwargs
    ) -> None:
        span._set_attribute(COMPONENT, self._integration_name)
        if model:
            span.set_tag("google_adk.request.model", model)
        if provider:
            span.set_tag("google_adk.request.provider", provider)

    def set_session_id(self, span: Span, session_id: Optional[str]) -> None:
        """Set the ADK session id as the LLMObs session for this span.

        Called at agent-span creation so the session id propagates to child tool and
        code-execute spans, which are created before the agent span finishes.
        """
        if not self.llmobs_enabled or not session_id:
            return
        _annotate_llmobs_span_data(span, session_id=session_id)

    def _llmobs_agent_name_at_start(self, span: Span, **kwargs: Any) -> Optional[str]:
        agent = kwargs.get("_dd_agent")
        return getattr(agent, "name", None) if agent else None

    def _llmobs_set_tags(
        self,
        span: Span,
        args: list[Any],
        kwargs: dict[str, Any],
        response: Optional[Any] = None,
        operation: str = "",  # one of "agent", "tool", "code_execute"
    ) -> None:
        # Set the span kind before the extraction below, which may raise on malformed data. The
        # caller swallows that exception, so kind must be set first or the span is dropped for
        # "missing span kind" and its children orphaned (#18698).
        _annotate_llmobs_span_data(
            span,
            kind=self._OPERATION_TO_SPAN_KIND.get(operation, operation),
            model_name=span.get_tag("google_adk.request.model") or "",
            model_provider=span.get_tag("google_adk.request.provider") or "",
        )

        if operation == "agent":
            self._llmobs_set_tags_agent(span, args, kwargs, response)
        elif operation == "tool":
            self._llmobs_set_tags_tool(span, args, kwargs, response)
        elif operation == "code_execute":
            self._llmobs_set_tags_code_execute(span, args, kwargs, response)

    def _llmobs_set_tags_agent(
        self, span: Span, args: list[Any], kwargs: dict[str, Any], response: Optional[Any]
    ) -> None:
        agent_instance = kwargs.get("instance", None)
        agent_name = getattr(agent_instance, "name", None)

        self._tag_agent_manifest(span, kwargs, agent_instance)
        new_message = get_argument_value(args, kwargs, 0, "new_message", optional=True) or []
        new_message_parts: list = getattr(new_message, "parts", [])
        new_message_role: str = getattr(new_message, "role", "")
        message = ""
        for part in new_message_parts:
            message += extract_message_from_part_google_genai(part, new_message_role).get("content", "")
        result = extract_messages_from_adk_events(response)

        # Surface the ADK session metadata (user id, app name) as searchable span tags.
        session_tags = {}
        user_id = kwargs.get("user_id")
        if user_id:
            session_tags["user_id"] = user_id
        app_name = kwargs.get("app_name")
        if app_name:
            session_tags["app_name"] = app_name

        _annotate_llmobs_span_data(
            span,
            name=agent_name or "Google ADK Agent",
            input_value=message,
            output_value=result,
            tags=session_tags or None,
        )

    def _llmobs_set_tags_tool(
        self, span: Span, args: list[Any], kwargs: dict[str, Any], response: Optional[Any] = None
    ) -> None:
        tool = get_argument_value(args, kwargs, 0, "tool")
        tool_args = get_argument_value(args, kwargs, 1, "args")
        tool_call_id = getattr(kwargs.get("tool_context", {}), "function_call_id", "")

        tool_name = getattr(tool, "name", "")
        tool_description = getattr(tool, "description", "")

        _annotate_llmobs_span_data(
            span,
            output_value=response,
            name=tool_name,
            metadata={"description": tool_description},
            input_value=tool_args,
        )

        if tool_call_id:
            core.dispatch(
                DISPATCH_ON_TOOL_CALL,
                (
                    tool_name,
                    safe_json(tool_args),
                    "function",
                    span,
                    tool_call_id,
                ),
            )

    def _tag_agent_manifest(self, span: Span, kwargs: dict[str, Any], agent: Any) -> None:
        if not agent:
            return
        manifest = build_agent_manifest(
            FRAMEWORK_NAME,
            agent,
            (
                ("labels", _manifest_labels),
                ("instructions", _manifest_instructions),
                ("model", _manifest_model),
                ("tools", _manifest_tools),
                ("data_contracts", _manifest_data_contracts),
                ("handoffs", _manifest_handoffs),
                ("guardrails", _manifest_guardrails),
                ("agent_settings", _manifest_agent_settings),
            ),
            self._integration_name,
        )
        if manifest:
            _annotate_llmobs_span_data(span, agent_manifest=dict(manifest))

    def _llmobs_set_tags_code_execute(
        self, span: Span, args: list[Any], kwargs: dict[str, Any], response: Optional[Any] = None
    ) -> None:
        stdout = getattr(response, "stdout", None)
        stderr = getattr(response, "stderr", None)
        output = ""
        if stdout:
            output += stdout
        if stderr:
            output += "/n" + stderr

        code_input = get_argument_value(args, kwargs, 1, "code_execution_input")
        _annotate_llmobs_span_data(
            span,
            name="Google ADK Code Execute",
            input_value=getattr(code_input, "code", ""),
            output_value=output,
        )


FRAMEWORK_NAME = "Google ADK"

# GenerateContentConfig field to its generic model_settings key.
_GENERATE_CONTENT_CONFIG_KEYS = {
    "temperature": "temperature",
    "top_p": "top_p",
    "top_k": "top_k",
    "max_output_tokens": "max_tokens",
    "stop_sequences": "stop_sequences",
    "seed": "seed",
    "presence_penalty": "presence_penalty",
    "frequency_penalty": "frequency_penalty",
}

# JSON Schema names, so a signature-derived tool reads the same as one from a schema.
_JSON_SCHEMA_TYPES = {
    "str": "string",
    "int": "integer",
    "float": "number",
    "bool": "boolean",
    "list": "array",
    "tuple": "array",
    "set": "array",
    "frozenset": "array",
    "dict": "object",
}

# Framework-injected arguments, not ones the model fills in.
_IGNORED_TOOL_PARAMETERS = frozenset({"self", "cls", "tool_context", "input_stream"})
_IGNORED_TOOL_PARAMETER_TYPES = frozenset({"ToolContext", "CallbackContext"})


def _manifest_labels(agent: Any) -> AgentManifest:
    # ADK shows a sub-agent's description to its parent's model when choosing a transfer target,
    # which is the role handoff_description plays in the other integrations.
    return {
        "name": as_str(getattr(agent, "name", None)),
        "handoff_description": as_str(getattr(agent, "description", None)),
    }


def _manifest_instructions(agent: Any) -> AgentManifest:
    fields = instruction_fields(getattr(agent, "instruction", None))
    system_prompts: list[str] = []
    global_instruction = getattr(agent, "global_instruction", None)
    if isinstance(global_instruction, str) and global_instruction:
        system_prompts.append(global_instruction)
    elif callable(global_instruction):
        fields["extra_instructions"] = fields.get("extra_instructions", []) + [
            {"type": "dynamic_global_instruction", "name": callable_name(global_instruction)}
        ]
    static_text = _content_text(getattr(agent, "static_instruction", None))
    if static_text:
        system_prompts.append(static_text)
    fields["system_prompts"] = system_prompts
    return fields


def _content_text(content: Any) -> str:
    """Text of a genai ContentUnion: a str, a Part, a Content, or a list of those."""
    if isinstance(content, str):
        return content
    if isinstance(content, list):
        texts = [_content_text(item) for item in content]
        return "\n".join(text for text in texts if text)
    parts = getattr(content, "parts", None)
    if isinstance(parts, list):
        return _content_text(parts)
    return as_str(getattr(content, "text", None))


def _manifest_model(agent: Any) -> AgentManifest:
    model = getattr(agent, "model", None)
    model_name = model if isinstance(model, str) else as_str(getattr(model, "model", None))
    config = getattr(agent, "generate_content_config", None)
    settings = {key: getattr(config, field, None) for field, key in _GENERATE_CONTENT_CONFIG_KEYS.items()}
    return {"model": model_name, "model_settings": filter_model_settings(settings)}


def _manifest_tools(agent: Any) -> AgentManifest:
    tools: list[dict[str, Any]] = []
    capabilities: list[AgentCapability] = []
    for tool in getattr(agent, "tools", None) or []:
        if hasattr(tool, "get_tools") and not hasattr(tool, "name"):
            # A toolset resolves its tools at run time, so only its presence is declared.
            kind = "mcp" if "mcp" in type(tool).__name__.lower() else "custom"
            capabilities.append({"name": type(tool).__name__, "type": kind})
            continue
        if hasattr(tool, "name"):
            func = getattr(tool, "func", None)
            entry = normalize_tool(
                getattr(tool, "name", None),
                getattr(tool, "description", None),
                _function_parameters(func) if callable(func) else None,
            )
        elif callable(tool):
            # ADK wraps any callable, including bound methods and functools.partial, in a FunctionTool.
            doc = getattr(getattr(tool, "func", tool), "__doc__", None)
            entry = normalize_tool(callable_name(tool), doc, _function_parameters(tool))
        else:
            continue
        if entry:
            tools.append(entry)
    return {"tools": tools, "capabilities": capabilities}


def _function_parameters(fn: Any) -> dict[str, Any]:
    """{param: {type?, required?}} read off the signature. The function is never called."""
    try:
        signature = inspect.signature(fn)
    except (TypeError, ValueError):
        return {}
    parameters: dict[str, Any] = {}
    for name, param in signature.parameters.items():
        if name in _IGNORED_TOOL_PARAMETERS or param.kind in (param.VAR_POSITIONAL, param.VAR_KEYWORD):
            continue
        spec: dict[str, Any] = {}
        if param.annotation is not param.empty:
            annotation_type = _annotation_type(param.annotation)
            if annotation_type in _IGNORED_TOOL_PARAMETER_TYPES:
                continue
            spec["type"] = annotation_type
        if param.default is param.empty:
            spec["required"] = True
        parameters[name] = spec
    return parameters


def _annotation_type(annotation: Any) -> str:
    """The JSON Schema name the schema-based integrations report for the same parameter.

    Optional[X] reports X, since the missing required flag already says it is optional.
    """
    if isinstance(annotation, str):
        # A string annotation comes from a module using postponed evaluation.
        return _JSON_SCHEMA_TYPES.get(annotation, annotation)
    origin = get_origin(annotation)
    if origin is Union or origin is getattr(types, "UnionType", None):
        names = [_annotation_type(arg) for arg in get_args(annotation) if arg is not type(None)]
        return " | ".join(dict.fromkeys(names))
    name = type_name(origin) if origin in (list, dict, tuple, set, frozenset) else type_name(annotation)
    return _JSON_SCHEMA_TYPES.get(name, name)


def _manifest_data_contracts(agent: Any) -> AgentManifest:
    contracts: dict[str, Any] = {}
    for key, attr in (("input", "input_schema"), ("output", "output_schema")):
        schema = getattr(agent, attr, None)
        if isinstance(schema, type):
            contracts[key] = {"name": type_name(schema)}
    return {"data_contracts": contracts}


def _manifest_handoffs(agent: Any) -> AgentManifest:
    handoffs: list[dict[str, Any]] = []
    for sub_agent in getattr(agent, "sub_agents", None) or []:
        name = as_str(getattr(sub_agent, "name", None))
        if name:
            handoffs.append(
                {"agent_name": name, "handoff_description": as_str(getattr(sub_agent, "description", None))}
            )
    return {"handoffs": handoffs}


def _manifest_guardrails(agent: Any) -> AgentManifest:
    """Callbacks that can veto a model or tool call before it runs, which is how ADK does guardrails."""
    guardrails: list[str] = []
    for attr in ("before_model_callback", "before_tool_callback"):
        callbacks = getattr(agent, attr, None)
        for callback in callbacks if isinstance(callbacks, list) else [callbacks]:
            if callable(callback):
                guardrails.append(callable_name(callback))
    return {"guardrails": guardrails}


def _manifest_agent_settings(agent: Any) -> AgentManifest:
    settings: dict[str, Any] = {}
    for attr in ("disallow_transfer_to_parent", "disallow_transfer_to_peers"):
        if getattr(agent, attr, None) is True:
            settings[attr] = True
    include_contents = getattr(agent, "include_contents", None)
    if isinstance(include_contents, str) and include_contents != "default":
        settings["include_contents"] = include_contents
    settings["output_key"] = as_str(getattr(agent, "output_key", None))
    max_iterations = getattr(agent, "max_iterations", None)
    if is_number(max_iterations):
        settings["max_iterations"] = max_iterations
    for attr in ("planner", "code_executor"):
        value = getattr(agent, attr, None)
        if value is not None:
            settings[attr] = type(value).__name__
    return {"agent_settings": settings}
