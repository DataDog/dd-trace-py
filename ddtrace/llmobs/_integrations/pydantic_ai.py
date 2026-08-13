import functools
from typing import Any
from typing import Optional
from typing import Sequence
from typing import cast
from typing import get_origin

from ddtrace.internal import core
from ddtrace.internal.logger import get_logger
from ddtrace.internal.utils import get_argument_value
from ddtrace.llmobs._constants import DISPATCH_ON_TOOL_CALL
from ddtrace.llmobs._integrations.agent_manifest import ALLOWED_MODEL_SETTINGS_KEYS
from ddtrace.llmobs._integrations.agent_manifest import callable_name
from ddtrace.llmobs._integrations.agent_manifest import is_flat_scalar_value
from ddtrace.llmobs._integrations.agent_manifest import is_number
from ddtrace.llmobs._integrations.agent_manifest import put_field
from ddtrace.llmobs._integrations.agent_manifest import type_name
from ddtrace.llmobs._integrations.agent_manifest import wire_value
from ddtrace.llmobs._integrations.base import BaseLLMIntegration
from ddtrace.llmobs._utils import _annotate_llmobs_span_data
from ddtrace.llmobs._utils import _get_attr
from ddtrace.llmobs._utils import get_llmobs_span_kind
from ddtrace.llmobs._utils import safe_json
from ddtrace.llmobs.types import AgentManifest
from ddtrace.trace import Span


log = get_logger(__name__)


# in some cases, PydanticAI uses a different provider name than what we expect
PYDANTIC_AI_SYSTEM_TO_PROVIDER = {
    "google-gla": "google",
    "google-vertex": "google",
}

FRAMEWORK_NAME = "PydanticAI"
_OUTPUT_MARKERS = frozenset({"ToolOutput", "NativeOutput", "PromptedOutput", "TextOutput"})


def _iter_agent_tools(agent: Any):
    """Yield (name, tool) for the agent's function tools, de-duped first-wins, across versions."""
    seen: set[str] = set()
    tool_dicts: list[dict[str, Any]] = []
    function_tools = getattr(agent, "_function_tools", None)
    if function_tools:
        tool_dicts.append(function_tools)
    else:
        function_toolset = getattr(agent, "_function_toolset", None)
        user_toolsets: Sequence[Any] = getattr(agent, "_user_toolsets", None) or []
        # Only a FunctionToolset exposes a {name: tool} dict; others are captured as custom.
        fn_cls = PydanticAIIntegration._function_toolset_cls()
        toolsets = [t for t in user_toolsets if fn_cls is None or isinstance(t, fn_cls)]
        if function_toolset is not None:
            toolsets.append(function_toolset)
        for toolset in toolsets:
            tools = getattr(toolset, "tools", None)
            if isinstance(tools, dict):
                tool_dicts.append(tools)
    for tools in tool_dicts:
        for name, tool in tools.items():
            if name in seen:
                continue
            seen.add(name)
            yield name, tool


def _collect_instructions(agent: Any) -> tuple[list[str], list[Any]]:
    """Gather (static_texts, dynamic_resolvers) from an agent's instructions.

    _instructions is a str below 1.63.0 and a mixed list after, so a resolver sits in
    _instructions_functions below and inline in _instructions after. The two are populated
    exclusively, never both, so reading each in turn cannot double-count.
    """
    static_texts: list[str] = []
    dynamic: list[Any] = []
    instructions = getattr(agent, "_instructions", None)
    if isinstance(instructions, (list, tuple)):
        for entry in instructions:
            if isinstance(entry, str):
                static_texts.append(entry)
            elif callable(entry):
                dynamic.append(entry)
    elif isinstance(instructions, str):
        static_texts.append(instructions)
    elif callable(instructions):
        dynamic.append(instructions)
    for runner in getattr(agent, "_instructions_functions", None) or []:
        fn = getattr(runner, "function", runner)
        if callable(fn):
            dynamic.append(fn)
    return static_texts, dynamic


def _collect_dynamic_system_prompts(agent: Any) -> list[Any]:
    """Dynamic system-prompt resolvers. Static prompts are read straight off agent._system_prompts."""
    dynamic: list[Any] = []
    for runner in getattr(agent, "_system_prompt_functions", None) or []:
        fn = getattr(runner, "function", runner)
        if callable(fn):
            dynamic.append(fn)
    return dynamic


class PydanticAIIntegration(BaseLLMIntegration):
    _integration_name = "pydantic_ai"
    _running_agents: dict[int, list[int]] = {}  # dictionary mapping agent span ID to tool span ID(s)
    _latest_agent = None  # str representing the span ID of the latest agent that was started
    _run_stream_active = False  # bool indicating if the latest agent span was generated from run_stream

    def trace(self, operation_id: str, submit_to_llmobs: bool = False, **kwargs: Any) -> Span:
        span = super().trace(operation_id, submit_to_llmobs, **kwargs)
        kind = kwargs.get("kind", None)
        if kind:
            self._register_span(span, kind)
            _annotate_llmobs_span_data(span, kind=kind)
        return span

    def _set_base_span_tags(self, span: Span, model: Optional[Any] = None, **kwargs) -> None:
        if model:
            model_name, provider = self._get_model_and_provider(model)
            span.set_tag("pydantic_ai.request.model", model_name)
            if provider:
                span.set_tag("pydantic_ai.request.provider", provider)

    def _get_model_and_provider(self, model: Optional[Any]) -> tuple[str, str]:
        model_name = getattr(model, "model_name", "")
        system = getattr(model, "system", None)
        if system:
            system = PYDANTIC_AI_SYSTEM_TO_PROVIDER.get(system, system)
        return model_name, system

    def _llmobs_set_tags(
        self,
        span: Span,
        args: list[Any],
        kwargs: dict[str, Any],
        response: Optional[Any] = None,
        operation: str = "",
    ) -> None:
        span_kind = get_llmobs_span_kind(span)

        if span_kind == "agent":
            self._llmobs_set_tags_agent(span, args, kwargs, response)
        elif span_kind == "tool":
            self._llmobs_set_tags_tool(span, args, kwargs, response)

        _annotate_llmobs_span_data(
            span,
            kind=span_kind,
            model_name=span.get_tag("pydantic_ai.request.model") or "",
            model_provider=span.get_tag("pydantic_ai.request.provider") or "",
        )

    def _llmobs_set_tags_agent(
        self, span: Span, args: list[Any], kwargs: dict[str, Any], response: Optional[Any]
    ) -> None:
        from pydantic_ai.agent import AgentRun

        agent_instance = kwargs.get("instance", None)
        agent_name = getattr(agent_instance, "name", None)
        user_prompt = get_argument_value(args, kwargs, 0, "user_prompt", optional=True)
        # AIDEV-NOTE: When callers like VercelAIAdapter pass all messages via message_history
        # without setting user_prompt, we fall back to extracting the last user message from
        # message_history. See https://github.com/DataDog/dd-trace-py/issues/16400
        if user_prompt is None:
            user_prompt = self._extract_user_prompt_from_message_history(kwargs)
        result = response
        if isinstance(result, AgentRun) and hasattr(result, "result"):
            result = getattr(result.result, "output", "")
        elif isinstance(result, tuple) and len(result) == 2:
            model_response, _ = result
            result = ""
            for part in getattr(model_response, "parts", []):
                if hasattr(part, "content"):
                    result += part.content
                elif hasattr(part, "args_as_json_str"):
                    result += part.args_as_json_str()
        _annotate_llmobs_span_data(
            span,
            name=agent_name or "PydanticAI Agent",
            input_value=user_prompt,
            output_value=result,
        )
        # Manifest last: the annotate above is not failure-isolated, so a manifest problem must
        # not cost the span's name, input and output.
        self._tag_agent_manifest(span, kwargs, agent_instance)

    @staticmethod
    def _extract_user_prompt_from_message_history(kwargs: dict[str, Any]) -> Optional[str]:
        """Extract the last user prompt from message_history when user_prompt is not provided."""
        message_history = kwargs.get("message_history")
        if not message_history:
            return None
        for message in reversed(message_history):
            for part in reversed(getattr(message, "parts", [])):
                if getattr(part, "part_kind", None) == "user-prompt":
                    content = getattr(part, "content", None)
                    if content is not None:
                        return str(content)
        return None

    def _llmobs_set_tags_tool(
        self, span: Span, args: list[Any], kwargs: dict[str, Any], response: Optional[Any] = None
    ) -> None:
        tool_instance = kwargs.get("instance", None)
        raw_call = (
            get_argument_value(args, kwargs, 0, "call", optional=True)
            or get_argument_value(args, kwargs, 0, "message", optional=True)
            or get_argument_value(args, kwargs, 0, "validated", optional=True)
        )
        # unwrap ValidatedToolCall into tool_instance and tool_call for newer versions of Pydantic AI
        if raw_call is not None and hasattr(raw_call, "args_valid"):
            if tool_instance is None:
                tool_instance = getattr(raw_call, "tool", None)
            tool_call = getattr(raw_call, "call", raw_call)
        else:
            tool_call = raw_call
        tool_name = "PydanticAI Tool"
        tool_input: Any = {}
        tool_id = ""
        if tool_call:
            tool_name = _get_attr(tool_call, "tool_name", "")
            tool_input = _get_attr(tool_call, "args", "") or ""
            tool_id = _get_attr(tool_call, "tool_call_id", "")
        tool_def = _get_attr(tool_instance, "tool_def", None)
        tool_description = (
            _get_attr(tool_def, "description", "") if tool_def else _get_attr(tool_instance, "description", "")
        )
        # str-only: the encoder reprs what it cannot encode, which can disclose the object.
        if not isinstance(tool_description, str):
            tool_description = ""

        output_val = None
        if not span.error:
            # depending on the version, the output may be a ToolReturnPart or the raw response
            output_val = getattr(response, "content", "") or response

        _annotate_llmobs_span_data(
            span,
            name=tool_name,
            metadata={"description": tool_description},
            input_value=tool_input,
            output_value=output_val,
        )

        core.dispatch(
            DISPATCH_ON_TOOL_CALL,
            (
                tool_name,
                safe_json(tool_input) if not isinstance(tool_input, str) else tool_input,
                "function",
                span,
                tool_id,
            ),
        )

    def _tag_agent_manifest(self, span: Span, kwargs: dict[str, Any], agent: Any) -> None:
        if not agent:
            return
        # dict() rather than a cast: the consumer takes a plain mapping, and a TypedDict is not
        # assignable to dict[str, Any] because a dict value is invariant.
        _annotate_llmobs_span_data(span, agent_manifest=dict(self._build_agent_manifest(agent)))

    def _build_agent_manifest(self, agent: Any) -> AgentManifest:
        """Build the shared agent manifest from a pydantic-ai Agent.

        Sections are built independently so a framework change inside one cannot blank the rest. Only
        declared configuration is read, so the manifest is identical run to run, and a field
        pydantic-ai does not expose is omitted rather than invented.
        """
        manifest: dict[str, Any] = {}
        for name, section in (
            ("labels", self._manifest_labels),
            ("instructions", self._manifest_instructions),
            ("model", self._manifest_model),
            ("capabilities", self._manifest_capabilities),
            ("data_contracts", self._manifest_data_contracts),
            ("memory_policies", self._manifest_memory_policies),
            ("guardrails", self._manifest_guardrails),
            ("agent_settings", self._manifest_agent_settings),
        ):
            try:
                manifest.update(section(agent))
            except Exception:
                log.debug("failed to build pydantic_ai agent manifest section %s", name, exc_info=True)
        # Cast rather than build a TypedDict directly: sections are assembled independently and
        # merged, and test_shape_is_one_flat_document is what actually enforces the key set.
        return cast(AgentManifest, manifest)

    def _manifest_labels(self, agent: Any) -> dict[str, Any]:
        """Labels that name the agent. Grouped for failure isolation only; the manifest is flat."""
        fields: dict[str, Any] = {"framework": FRAMEWORK_NAME}
        # AIDEV-NOTE: placeholder per review, matching the span name fallback. Two unnamed agents
        # therefore share it, so name is not an identity.
        agent_name = getattr(agent, "name", None)
        put_field(fields, "name", agent_name if isinstance(agent_name, str) and agent_name else "PydanticAI Agent")
        metadata = getattr(agent, "_metadata", None)
        # metadata may be a callable from 1.39.0 on. Only a static dict is captured; the resolver
        # is never invoked.
        put_field(fields, "metadata", wire_value(metadata) if isinstance(metadata, dict) else None)
        return fields

    def _manifest_instructions(self, agent: Any) -> dict[str, Any]:
        """What the agent is told. A resolver's text is only known at run time, so it ships by name."""
        fields: dict[str, Any] = {}
        static_texts, dynamic_instructions = _collect_instructions(agent)
        put_field(fields, "instructions", "\n".join(text for text in static_texts if text))
        # Not validated upstream; a non-string would ship as a repr.
        prompts = [p for p in (getattr(agent, "_system_prompts", None) or ()) if isinstance(p, str)]
        put_field(fields, "system_prompts", prompts)
        extra: list[dict[str, Any]] = []
        for kind, resolvers in (
            ("dynamic_instructions", dynamic_instructions),
            ("dynamic_system_prompt", _collect_dynamic_system_prompts(agent)),
        ):
            extra.extend({"type": kind, "name": callable_name(fn)} for fn in resolvers)
        put_field(fields, "extra_instructions", extra)
        return fields

    def _manifest_model(self, agent: Any) -> dict[str, Any]:
        """The model and the inference params the user set, filtered by ALLOWED_MODEL_SETTINGS_KEYS."""
        fields: dict[str, Any] = {}
        model = getattr(agent, "model", None)
        if isinstance(model, str):
            # First colon, not last: rpartition reads "bedrock:anthropic.claude-v1:0" as "0".
            _, _, declared_name = model.partition(":")
            put_field(fields, "model", declared_name or model)
        elif model:
            model_name, _ = self._get_model_and_provider(model)
            # AIDEV-NOTE: str-only, for the same reason as the tool description read. model_name is
            # annotated str, but a custom Model subclass returns whatever it likes and the encoder
            # reprs what it cannot encode, which can carry a connection string.
            put_field(fields, "model", model_name if isinstance(model_name, str) else None)
        settings = getattr(agent, "model_settings", None)
        if isinstance(settings, dict):
            allowed: dict[str, Any] = {}
            for key, value in settings.items():
                if key not in ALLOWED_MODEL_SETTINGS_KEYS or not is_flat_scalar_value(value):
                    continue
                # Via put_field: wire_value returns None for what it cannot encode, and a direct
                # assignment would ship that as an explicit null.
                put_field(allowed, key, wire_value(value))
            put_field(fields, "model_settings", allowed)
        return fields

    def _manifest_capabilities(self, agent: Any) -> dict[str, Any]:
        """Function tools, plus the powers that are not plain functions, by name."""
        fields: dict[str, Any] = {}
        put_field(fields, "tools", self._get_agent_tools(agent))
        prepared = [
            fn
            for fn in (getattr(agent, "_prepare_tools", None), getattr(agent, "_prepare_output_tools", None))
            if callable(fn)
        ]
        capabilities: list[dict[str, Any]] = []
        for kind, names in (
            ("mcp", self._mcp_server_names(agent)),
            ("builtin", self._builtin_tool_names(agent)),
            ("custom", self._toolset_names(agent)),
            ("tool_preparation", [callable_name(fn) for fn in prepared]),
        ):
            capabilities.extend({"name": name, "type": kind} for name in names if name)
        put_field(fields, "capabilities", capabilities)
        return fields

    def _manifest_data_contracts(self, agent: Any) -> dict[str, Any]:
        """The declared output type by name. pydantic-ai declares no input schema."""
        name = self._output_type_name(agent)
        return {"data_contracts": {"output": {"name": name}}} if name else {}

    def _manifest_memory_policies(self, agent: Any) -> dict[str, Any]:
        """The message-history pipeline, order preserved: [trim, summarize] is not [summarize, trim].

        A repeat is kept for the same reason order is: [trim, trim] runs trim twice.
        """
        fields: dict[str, Any] = {}
        processors = [fn for fn in getattr(agent, "history_processors", None) or [] if callable(fn)]
        put_field(fields, "memory_policies", [callable_name(fn) for fn in processors])
        return fields

    def _manifest_guardrails(self, agent: Any) -> dict[str, Any]:
        """Output validators by name, matching the shape the other integrations already emit."""
        fields: dict[str, Any] = {}
        validators = getattr(agent, "_output_validators", None) or []
        fns = [getattr(v, "function", v) for v in validators]
        put_field(fields, "guardrails", [callable_name(fn) for fn in fns if callable(fn)])
        return fields

    def _manifest_agent_settings(self, agent: Any) -> dict[str, Any]:
        """Loop-level knobs, as opposed to model params.

        retries is the output-validation budget and tool_retries the per-tool one, so
        Agent(retries=3, output_retries=2) reports retries 2 with tool_retries 3.
        """
        settings: dict[str, Any] = {}
        # 1.107.1 renamed _max_result_retries to _max_output_retries, so fall back to the successor.
        retries = getattr(agent, "_max_result_retries", None)
        if not is_number(retries):
            retries = getattr(agent, "_max_output_retries", None)
        for name, value in (
            ("retries", retries),
            ("tool_retries", getattr(agent, "_max_tool_retries", None)),
            ("tool_timeout", getattr(agent, "_tool_timeout", None)),
            # The parameter is not retained; it is normalized into a limiter at construction, and that
            # limiter is None when unset, which keeps "unset" distinct from a real value.
            ("max_concurrency", getattr(getattr(agent, "_concurrency_limiter", None), "max_running", None)),
        ):
            if is_number(value):
                settings[name] = value
        end_strategy = getattr(agent, "end_strategy", None)
        if isinstance(end_strategy, str):
            put_field(settings, "end_strategy", end_strategy)
        deps_type = getattr(agent, "_deps_type", None)
        # Omit the "no deps" default, NoneType below 2.x and object from 2.x on, so it is not noise.
        if isinstance(deps_type, type) and deps_type not in (type(None), object):
            put_field(settings, "deps_type", deps_type.__name__)
        return {"agent_settings": settings} if settings else {}

    def _get_agent_tools(self, agent: Any) -> list[dict[str, Any]]:
        """Function tools as {name, description?, parameters?}, each exactly once.

        For pydantic-ai below 0.4.4 tools live on the agent's _function_tools. From 0.4.4 on they live
        on _function_toolset and on any user-supplied FunctionToolset in _user_toolsets.
        """
        tools: list[dict[str, Any]] = []
        for tool_name, tool_instance in _iter_agent_tools(agent):
            entry: dict[str, Any] = {"name": tool_name if isinstance(tool_name, str) else str(tool_name)}
            # AIDEV-NOTE: str-only. pydantic-ai accepts a non-str description and the encoder reprs
            # what it cannot encode, which can carry credentials.
            description = getattr(tool_instance, "description", None)
            put_field(entry, "description", description if isinstance(description, str) else None)
            put_field(entry, "parameters", self._tool_parameters(tool_instance))
            tools.append(entry)
        return tools

    @staticmethod
    def _tool_parameters(tool_instance: Any) -> dict[str, dict[str, Any]]:
        """Extract {param: {type?, required?}} from a tool's function_schema.json_schema."""
        function_schema = getattr(tool_instance, "function_schema", {})
        json_schema = getattr(function_schema, "json_schema", {})
        if not isinstance(json_schema, dict):
            return {}
        required = json_schema.get("required")
        required_params = {str(param) for param in required} if isinstance(required, (list, tuple, set)) else set()
        properties = json_schema.get("properties")
        if not isinstance(properties, dict):
            return {}
        parameters: dict[str, dict[str, Any]] = {}
        for param, schema in properties.items():
            # Keys coerced: Tool.from_schema takes a caller json_schema, so a non-str key reaches
            # here. The span sanitizer stringifies keys too, so this is belt and braces.
            param_dict: dict[str, Any] = {}
            if isinstance(schema, dict):
                put_field(param_dict, "type", wire_value(schema.get("type")))
            if str(param) in required_params:
                param_dict["required"] = True
            parameters[str(param)] = param_dict
        return parameters

    def _builtin_tool_names(self, agent: Any) -> list[str]:
        """Provider-side builtin tools by name. _builtin_tools is gone from 2.x, so the key drops."""
        tools = getattr(agent, "_builtin_tools", None) or []
        return [getattr(tool, "kind", None) or type(tool).__name__ for tool in tools]

    def _toolset_names(self, agent: Any) -> list[str]:
        """Toolsets that are neither function tools nor MCP servers, so none is silently dropped."""
        mcp_classes = self._mcp_server_classes()
        fn_cls = self._function_toolset_cls()
        names: list[str] = []
        for toolset in getattr(agent, "_user_toolsets", None) or []:
            if (mcp_classes and isinstance(toolset, mcp_classes)) or (fn_cls and isinstance(toolset, fn_cls)):
                continue
            names.append(self._toolset_name(toolset))
        for toolset in getattr(agent, "_dynamic_toolsets", None) or []:
            fn = getattr(toolset, "toolset_func", None)
            names.append(callable_name(fn) if callable(fn) else self._toolset_name(toolset))
        return names

    def _output_type_name(self, agent: Any) -> str:
        """The declared output type by name. An output function is not a declared type."""
        if not hasattr(agent, "output_type"):
            return ""
        candidates = [c for c in self._unwrap_output_markers(agent.output_type) if not self._is_output_function(c)]
        return " | ".join(type_name(c) for c in candidates)

    @staticmethod
    @functools.lru_cache(maxsize=1)
    def _mcp_server_classes() -> tuple[type, ...]:
        """Every MCP class this pydantic-ai defines, for isinstance filtering.

        All present names, not the first found: at 1.107.x MCPServer and MCPToolset are unrelated
        subclasses and matching one files the other as a plain toolset.
        """
        try:
            import pydantic_ai.mcp as mcp_module
        except Exception:  # noqa: BLE001 - the optional mcp extra may not be installed
            return ()
        classes: list[type] = []
        for name in ("MCPServer", "MCPToolset"):
            candidate = getattr(mcp_module, name, None)
            if isinstance(candidate, type):
                classes.append(candidate)
        return tuple(classes)

    @staticmethod
    @functools.lru_cache(maxsize=1)
    def _function_toolset_cls() -> Optional[type]:
        """FunctionToolset for isinstance filtering, or None, in which case nothing is filtered out."""
        try:
            from pydantic_ai.toolsets import FunctionToolset
        except Exception:  # noqa: BLE001 - the toolset module layout varies by version
            return None
        fn_cls: type = FunctionToolset
        return fn_cls

    @staticmethod
    def _toolset_name(toolset: Any) -> str:
        """Toolset or MCP server name: the id the user set, else the class name.

        AIDEV-NOTE: never read label. Without an id it falls back to repr(self), which carries the
        connection config, so only an explicit str id or the class name ships. No URI is emitted at
        all, which is what keeps a credential in a server's userinfo, path or query off the wire.
        """
        try:
            toolset_id = getattr(toolset, "id", None)
        except Exception:  # noqa: BLE001 - id is a property on some toolsets and may raise
            toolset_id = None
        # Require a real string: an id can be any object on a custom toolset.
        return toolset_id if isinstance(toolset_id, str) and toolset_id else type(toolset).__name__

    def _mcp_server_names(self, agent: Any) -> list[str]:
        """MCP servers by name. No URI: a server address can carry credentials in any component."""
        mcp_classes = self._mcp_server_classes()
        if not mcp_classes:
            return []
        toolsets = getattr(agent, "_user_toolsets", None) or []
        return [self._toolset_name(t) for t in toolsets if isinstance(t, mcp_classes)]

    @staticmethod
    def _unwrap_output_markers(output_type: Any) -> list[Any]:
        """Candidate output types, with any ToolOutput-style wrapper replaced by what it wraps.

        Matched by class name rather than isinstance: the wrapper attrs are not exclusive to markers,
        so a dataclass with an "output" field would otherwise be unwrapped into its own member.
        """
        candidates: list[Any] = []
        for item in output_type if isinstance(output_type, (list, tuple)) else [output_type]:
            inner = item
            if type(item).__name__ in _OUTPUT_MARKERS:
                inner = next(
                    (v for a in ("output", "outputs", "output_function") if (v := getattr(item, a, None))), item
                )
            candidates.extend(inner if isinstance(inner, (list, tuple)) else [inner])
        return candidates

    @staticmethod
    def _is_output_function(candidate: Any) -> bool:
        """Callable but not a class. The get_origin check keeps list[Fruit] from reading as one."""
        if get_origin(candidate) is not None:
            return False
        return callable(candidate) and not isinstance(candidate, type)

    def _register_span(self, span: Span, kind: Any) -> None:
        if kind == "agent":
            self._register_agent(span)
        elif kind == "tool":
            self._register_tool(span)

    def _register_agent(self, span: Span) -> None:
        self._latest_agent = span.span_id
        self._running_agents[span.span_id] = []

    def _register_tool(self, span: Span) -> None:
        if self._latest_agent is not None:
            self._running_agents[self._latest_agent].append(span.span_id)
