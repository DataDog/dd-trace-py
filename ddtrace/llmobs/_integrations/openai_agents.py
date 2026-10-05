import json
from typing import Any
from typing import Optional
from typing import Union
from typing import get_origin
import weakref

from ddtrace.internal import core
from ddtrace.internal.logger import get_logger
from ddtrace.internal.utils.formats import format_trace_id
from ddtrace.llmobs._constants import DISPATCH_ON_GUARDRAIL_SPAN_START
from ddtrace.llmobs._constants import DISPATCH_ON_LLM_TOOL_CHOICE
from ddtrace.llmobs._constants import DISPATCH_ON_OPENAI_AGENT_SPAN_FINISH
from ddtrace.llmobs._constants import DISPATCH_ON_TOOL_CALL
from ddtrace.llmobs._constants import DISPATCH_ON_TOOL_CALL_OUTPUT_USED
from ddtrace.llmobs._constants import OAI_HANDOFF_TOOL_ARG
from ddtrace.llmobs._constants import ROOT_PARENT_ID
from ddtrace.llmobs._integrations.agent_manifest import ALLOWED_MODEL_SETTINGS_KEYS
from ddtrace.llmobs._integrations.agent_manifest import as_str
from ddtrace.llmobs._integrations.agent_manifest import build_agent_manifest
from ddtrace.llmobs._integrations.agent_manifest import callable_name
from ddtrace.llmobs._integrations.agent_manifest import config_value
from ddtrace.llmobs._integrations.agent_manifest import filter_model_settings
from ddtrace.llmobs._integrations.agent_manifest import instruction_fields
from ddtrace.llmobs._integrations.agent_manifest import normalize_tool
from ddtrace.llmobs._integrations.agent_manifest import type_name
from ddtrace.llmobs._integrations.base import BaseLLMIntegration
from ddtrace.llmobs._integrations.utils import LLMObsTraceInfo
from ddtrace.llmobs._integrations.utils import OaiSpanAdapter
from ddtrace.llmobs._integrations.utils import OaiTraceAdapter
from ddtrace.llmobs._utils import _annotate_llmobs_span_data
from ddtrace.llmobs._utils import _get_nearest_llmobs_ancestor
from ddtrace.llmobs._utils import get_llmobs_parent_id
from ddtrace.llmobs._utils import get_llmobs_span_name
from ddtrace.llmobs._utils import get_tool_version_from_llm_span
from ddtrace.llmobs._utils import safe_json
from ddtrace.llmobs.types import AgentCapability
from ddtrace.llmobs.types import AgentInstructionResolver
from ddtrace.llmobs.types import AgentManifest
from ddtrace.trace import Span


logger = get_logger(__name__)


class OpenAIAgentsIntegration(BaseLLMIntegration):
    _integration_name = "openai_agents"

    def __init__(self, integration_config: Any) -> None:
        super().__init__(integration_config)
        # a map of openai span ids to the corresponding llm obs span
        self.oai_to_llmobs_span: weakref.WeakValueDictionary[str, Span] = weakref.WeakValueDictionary()
        # a map of LLM Obs trace ids to LLMObsTraceInfo which stores metadata about the trace
        # used to set attributes on the root span of the trace.
        self.llmobs_traces: dict[str, LLMObsTraceInfo] = {}

    def trace(
        self,
        operation_id: str = "",
        submit_to_llmobs: bool = False,
        **kwargs,
    ) -> Span:
        oai_trace = kwargs.get("oai_trace")
        oai_span = kwargs.get("oai_span")

        span_name = oai_trace.name if oai_trace else oai_span.name if oai_span else "openai_agents.request"

        llmobs_span = super().trace(
            operation_id=operation_id or span_name,
            submit_to_llmobs=submit_to_llmobs,
            span_name=span_name,
        )
        if oai_trace:
            self.oai_to_llmobs_span[oai_trace.trace_id] = llmobs_span
            self.llmobs_traces[format_trace_id(llmobs_span.trace_id)] = LLMObsTraceInfo(
                span_id=str(llmobs_span.span_id),
                trace_id=format_trace_id(llmobs_span.trace_id),
            )
        elif oai_span:
            self.oai_to_llmobs_span[oai_span.span_id] = llmobs_span
            self._llmobs_update_trace_info_input(oai_span, llmobs_span)

            # Stamp the agent kind at start (child spans resolve agent attribution against it).
            # Guard on llmobs_enabled: super().trace() returns a plain APM span when LLMObs is
            # off, and annotating a non-LLM span leaks meta_struct overhead into non-LLMObs traces.
            if oai_span.llmobs_span_kind == "agent" and self.llmobs_enabled:
                _annotate_llmobs_span_data(llmobs_span, kind="agent")

            if oai_span.span_type == "guardrail":
                core.dispatch(DISPATCH_ON_GUARDRAIL_SPAN_START, (llmobs_span,))

        return llmobs_span

    def _llmobs_set_tags(
        self,
        span: Span,
        args: list[Any],
        kwargs: dict[str, Union[Any, OaiTraceAdapter, OaiSpanAdapter]],
        response: Optional[Any] = None,
        operation: str = "",
    ) -> None:
        """Sets meta tags and metrics for span events to be sent to LLMObs."""
        oai_trace = kwargs.get("oai_trace")
        if oai_trace is not None and isinstance(oai_trace, OaiTraceAdapter):
            self._llmobs_set_trace_attributes(span, oai_trace)
            return

        oai_span = kwargs.get("oai_span")
        if not oai_span:
            return

        if not isinstance(oai_span, OaiSpanAdapter):
            logger.warning("Expected OaiSpanAdapter but got %s", type(oai_span))
            return

        span_type = oai_span.span_type
        span_kind = oai_span.llmobs_span_kind
        _annotate_llmobs_span_data(span, kind=span_kind)

        if oai_span.error:
            error_msg = oai_span.get_error_message()
            error_data = oai_span.get_error_data()
            span.error = 1
            """
            The error message from openai agents is actually a more concise description of the error.
            while the error data contains the full error object.
            Thus, we set the LLM Obs span's error type to the openai span's error message
            and the LLM Obs span's error message to the openai span's error data.
            """
            if error_msg:
                span.set_tag("error.type", error_msg)
            if error_data and error_msg:
                span.set_tag("error.message", json.dumps(error_data))

        if span_type == "response":
            self._llmobs_set_response_attributes(span, oai_span)
            self._llmobs_update_trace_info_output(oai_span)
        elif span_type in ("function", "tool"):
            self._llmobs_set_tool_attributes(span, oai_span)
        elif span_type == "handoff":
            self._llmobs_set_handoff_attributes(span, oai_span)
        elif span_type == "agent":
            self._llmobs_set_agent_attributes(span, oai_span)
            core.dispatch(DISPATCH_ON_OPENAI_AGENT_SPAN_FINISH, ())
        elif span_type == "custom":
            _annotate_llmobs_span_data(span, metadata=oai_span.formatted_custom_data or None)

    def _llmobs_update_trace_info_input(self, oai_span: OaiSpanAdapter, llmobs_span: Span) -> None:
        """
        Store the openai span we are using as the input to the top level trace. Since the openai trace
        itself does not have input / output data, we use the input to the first LLM call of the first
        agent invocation as the top level trace input.
        """
        trace_info = self._llmobs_get_trace_info(oai_span)
        if not trace_info:
            return
        parent_id = get_llmobs_parent_id(llmobs_span)
        if oai_span.span_type == "agent" and parent_id == trace_info.span_id:
            trace_info.current_top_level_agent_span_id = str(llmobs_span.span_id)
        if (
            oai_span.span_type == "response"
            and parent_id != ROOT_PARENT_ID
            and not trace_info.input_oai_span
            and parent_id == trace_info.current_top_level_agent_span_id
        ):
            trace_info.input_oai_span = oai_span

    def _llmobs_update_trace_info_output(self, oai_span: OaiSpanAdapter) -> None:
        """
        Store the openai span we are using as the output to the top level trace. Since the openai trace
        itself does not have input / output data, we use the output of the last LLM call of the last
        agent invocation as the top level trace output.
        """
        trace_info = self._llmobs_get_trace_info(oai_span)
        if not trace_info:
            return

        llmobs_span = self.oai_to_llmobs_span.get(oai_span.span_id)
        if not llmobs_span:
            return

        current_top_level_agent_span_id = trace_info.current_top_level_agent_span_id
        if current_top_level_agent_span_id and get_llmobs_parent_id(llmobs_span) == current_top_level_agent_span_id:
            trace_info.output_oai_span = oai_span

    def _llmobs_set_trace_attributes(self, span: Span, oai_trace: OaiTraceAdapter) -> None:
        trace_info = self._llmobs_get_trace_info(oai_trace)
        if not trace_info:
            return

        input_value = None
        if trace_info.input_oai_span:
            input_value = trace_info.input_oai_span.llmobs_trace_input() or None

        output_value = None
        if trace_info.output_oai_span:
            output_value = trace_info.output_oai_span.response_output_text or None

        _annotate_llmobs_span_data(
            span,
            kind="workflow",
            session_id=oai_trace.group_id or None,
            input_value=input_value,
            output_value=output_value,
            metadata=oai_trace.metadata or None,
        )

    def _llmobs_set_response_attributes(self, span: Span, oai_span: OaiSpanAdapter) -> None:
        """Sets attributes for response type spans."""
        if not oai_span.response:
            return

        name = None
        parent = _get_nearest_llmobs_ancestor(span)
        trace_info = self._llmobs_get_trace_info(oai_span)
        if parent and trace_info and get_llmobs_parent_id(span) == trace_info.current_top_level_agent_span_id:
            name = (get_llmobs_span_name(parent) or parent.name) + " (LLM)"

        input_messages = None
        if oai_span.input:
            input_messages, tool_call_ids = oai_span.llmobs_input_messages()
            for tool_call_id in tool_call_ids:
                core.dispatch(DISPATCH_ON_TOOL_CALL_OUTPUT_USED, (tool_call_id, span))

        output_messages = None
        if oai_span.response and oai_span.response.output:
            output_messages, tool_call_outputs, _ = oai_span.llmobs_output_messages()
            for tool_call_output in tool_call_outputs:
                core.dispatch(
                    DISPATCH_ON_LLM_TOOL_CHOICE,
                    (
                        tool_call_output.get("tool_id", ""),
                        tool_call_output.get("name", ""),
                        safe_json(tool_call_output.get("arguments", {})),
                        {
                            "trace_id": format_trace_id(span.trace_id),
                            "span_id": str(span.span_id),
                        },
                        get_tool_version_from_llm_span(span, tool_call_output.get("name", "")),
                    ),
                )

        _annotate_llmobs_span_data(
            span,
            name=name,
            model_name=oai_span.llmobs_model_name or None,
            model_provider="openai" if oai_span.llmobs_model_name else None,
            input_messages=input_messages,
            output_messages=output_messages,
            metadata=oai_span.llmobs_metadata or None,
            metrics=oai_span.llmobs_metrics or None,
        )

    def _llmobs_set_tool_attributes(self, span: Span, oai_span: OaiSpanAdapter) -> None:
        _annotate_llmobs_span_data(span, input_value=oai_span.input or "", output_value=oai_span.output or "")
        core.dispatch(
            DISPATCH_ON_TOOL_CALL,
            (oai_span.name, oai_span.input, "function", span),
        )

    def _llmobs_set_handoff_attributes(self, span: Span, oai_span: OaiSpanAdapter) -> None:
        handoff_tool_name = "transfer_to_{}".format("_".join(oai_span.to_agent.split(" ")).lower())
        span.name = handoff_tool_name
        _annotate_llmobs_span_data(
            span,
            name=handoff_tool_name,
            input_value=oai_span.from_agent or "",
            output_value=oai_span.to_agent or "",
        )
        core.dispatch(
            DISPATCH_ON_TOOL_CALL,
            (handoff_tool_name, OAI_HANDOFF_TOOL_ARG, "handoff", span),
        )

    def _llmobs_set_agent_attributes(self, span: Span, oai_span: OaiSpanAdapter) -> None:
        if oai_span.llmobs_metadata:
            _annotate_llmobs_span_data(span, metadata=oai_span.llmobs_metadata)

    def _llmobs_get_trace_info(
        self, oai_trace_or_span: Union[OaiSpanAdapter, OaiTraceAdapter]
    ) -> Optional[LLMObsTraceInfo]:
        """Get trace info for a span

        Args:
            oai_trace_or_span: An openai span or trace adapter to get trace info for.

        Returns:
            The trace info if found, None otherwise.
        """
        key = None
        if isinstance(oai_trace_or_span, OaiSpanAdapter):
            key = oai_trace_or_span.span_id
        elif isinstance(oai_trace_or_span, OaiTraceAdapter):
            key = oai_trace_or_span.trace_id
        else:
            return None

        llmobs_span = self.oai_to_llmobs_span.get(key)
        if not llmobs_span:
            return None

        return self.llmobs_traces.get(format_trace_id(llmobs_span.trace_id))

    def clear_state(self) -> None:
        self.oai_to_llmobs_span.clear()
        self.llmobs_traces.clear()

    # MLOB-7584 — the agent's position varies by wheel (0.0.x-0.17.x): ``agent=`` kwarg
    # (0.8-0.13), ``bindings=`` kwarg (>=0.14), or positional arg[1] when streamed (arg[0] is the
    # RunResultStreaming — no Agent attrs, so it's skipped). One scanner covers every shape.
    def _extract_agent_from_call(self, args: list[Any], kwargs: dict[str, Any]) -> Optional[Any]:
        """Resolve the Agent from a run_single_turn[_streamed] call across versions and call shapes."""
        candidates = []
        for key in ("bindings", "agent"):
            value = kwargs.get(key)
            if value is not None:
                candidates.append(value)
        candidates.extend(arg for arg in args if arg is not None)
        for candidate in candidates:
            # AgentBindings shape (>= 0.14.0). MLOB-7584 — the manifest is the user's DECLARED
            # config, so prefer ``public_agent`` over ``execution_agent`` (a possible sandbox-rewritten clone).
            bound_agent = getattr(candidate, "public_agent", None) or getattr(candidate, "execution_agent", None)
            if bound_agent is not None:
                return bound_agent
            # Bare Agent shape (0.0.x-0.13.x). Duck-typed (no stable Agent type across versions, so no
            # isinstance): name+tools+handoffs distinguishes it from RunResultStreaming (only current_agent).
            if hasattr(candidate, "name") and hasattr(candidate, "tools") and hasattr(candidate, "handoffs"):
                return candidate
        return None

    def tag_agent_manifest(self, span: Span, args: list[Any], kwargs: dict[str, Any]) -> None:
        agent = self._extract_agent_from_call(args, kwargs)
        if agent is None:
            return
        self._tag_agent_manifest_from_agent(span, agent)

    def _tag_agent_manifest_from_agent(self, span: Span, agent: Any) -> None:
        if not agent or not self.llmobs_enabled:
            return
        manifest = build_agent_manifest(
            FRAMEWORK_NAME,
            agent,
            (
                ("labels", _manifest_labels),
                ("instructions", _manifest_instructions),
                ("model", _manifest_model),
                ("tools", _manifest_tools),
                ("capabilities", _manifest_capabilities),
                ("data_contracts", _manifest_data_contracts),
                ("handoffs", _manifest_handoffs),
                ("guardrails", _manifest_guardrails),
                ("agent_settings", _manifest_agent_settings),
            ),
            self._integration_name,
        )
        if manifest:
            _annotate_llmobs_span_data(span, agent_manifest=dict(manifest))


FRAMEWORK_NAME = "OpenAI"

# Declared config worth diffing on each hosted tool. Everything else on these tools is a callable,
# a client object, or a credential (HostedMCPTool.tool_config carries headers and authorization).
_HOSTED_TOOL_FIELDS = {
    "file_search": ("vector_store_ids", "max_num_results", "include_search_results", "ranking_options", "filters"),
    "web_search": ("user_location", "search_context_size", "filters"),
    "web_search_preview": ("user_location", "search_context_size", "filters"),
}
_HOSTED_MCP_CONFIG_KEYS = ("server_label", "allowed_tools", "require_approval")


def _manifest_labels(agent: Any) -> AgentManifest:
    return {
        "name": as_str(getattr(agent, "name", None)),
        "handoff_description": as_str(getattr(agent, "handoff_description", None)),
    }


def _manifest_instructions(agent: Any) -> AgentManifest:
    fields = instruction_fields(getattr(agent, "instructions", None))
    prompt = getattr(agent, "prompt", None)
    # A stored prompt is resolved by the Responses API at run time, so it is recorded by id.
    resolver: Optional[AgentInstructionResolver] = None
    if isinstance(prompt, dict) and isinstance(prompt.get("id"), str):
        resolver = {"type": "prompt", "name": prompt["id"]}
    elif callable(prompt):
        resolver = {"type": "dynamic_prompt", "name": callable_name(prompt)}
    if resolver:
        fields["extra_instructions"] = fields.get("extra_instructions", []) + [resolver]
    return fields


def _manifest_model(agent: Any) -> AgentManifest:
    model = getattr(agent, "model", None)
    model_name = model if isinstance(model, str) else as_str(getattr(model, "model", None))
    settings = getattr(agent, "model_settings", None)
    # Read field by field rather than dumped, so extra_headers and extra_body are never touched.
    if settings is not None and not isinstance(settings, dict):
        settings = {key: getattr(settings, key, None) for key in ALLOWED_MODEL_SETTINGS_KEYS}
    return {"model": model_name, "model_settings": filter_model_settings(settings)}


def _manifest_tools(agent: Any) -> AgentManifest:
    tools: list[dict[str, Any]] = []
    for tool in getattr(agent, "tools", None) or []:
        name = getattr(tool, "name", None)
        if hasattr(tool, "params_json_schema"):
            entry = normalize_tool(name, getattr(tool, "description", None), tool.params_json_schema)
        else:
            entry = _hosted_tool(tool, name)
        if entry:
            tools.append(entry)
    return {"tools": tools}


def _hosted_tool(tool: Any, name: Any) -> Optional[dict[str, Any]]:
    if not isinstance(name, str) or not name:
        return None
    entry: dict[str, Any] = {"name": name}
    for field in _HOSTED_TOOL_FIELDS.get(name, ()):
        entry[field] = config_value(getattr(tool, field, None))
    if name == "hosted_mcp":
        tool_config = getattr(tool, "tool_config", None)
        if isinstance(tool_config, dict):
            for key in _HOSTED_MCP_CONFIG_KEYS:
                entry[key] = config_value(tool_config.get(key))
    return entry


def _manifest_capabilities(agent: Any) -> AgentManifest:
    capabilities: list[AgentCapability] = []
    for server in getattr(agent, "mcp_servers", None) or []:
        name = as_str(getattr(server, "name", None))
        if name:
            capabilities.append({"name": name, "type": "mcp"})
    return {"capabilities": capabilities}


def _manifest_data_contracts(agent: Any) -> AgentManifest:
    output_type = getattr(agent, "output_type", None)
    if output_type is None or output_type is str:
        return {}
    if isinstance(output_type, type) or get_origin(output_type) is not None:
        name = type_name(output_type)
    else:
        # An AgentOutputSchemaBase wraps the declared type; its class name is all that is stable.
        name = type(output_type).__name__
    return {"data_contracts": {"output": {"name": name}}}


def _manifest_handoffs(agent: Any) -> AgentManifest:
    handoffs: list[dict[str, Any]] = []
    for handoff in getattr(agent, "handoffs", None) or []:
        # A bare Agent describes itself through handoff_description; a Handoff carries the tool
        # description the calling model actually sees.
        entry = {
            "agent_name": as_str(getattr(handoff, "agent_name", None) or getattr(handoff, "name", None)),
            "tool_name": as_str(getattr(handoff, "tool_name", None)),
            "handoff_description": as_str(
                getattr(handoff, "handoff_description", None) or getattr(handoff, "tool_description", None)
            ),
        }
        if entry["agent_name"]:
            handoffs.append(entry)
    return {"handoffs": handoffs}


def _manifest_guardrails(agent: Any) -> AgentManifest:
    guardrails: list[str] = []
    for guardrail in list(getattr(agent, "input_guardrails", None) or []) + list(
        getattr(agent, "output_guardrails", None) or []
    ):
        name = getattr(guardrail, "name", None)
        fn = getattr(guardrail, "guardrail_function", None)
        if not name and callable(fn):
            name = callable_name(fn)
        if isinstance(name, str) and name:
            guardrails.append(name)
    return {"guardrails": guardrails}


def _manifest_agent_settings(agent: Any) -> AgentManifest:
    settings: dict[str, Any] = {}
    behavior = getattr(agent, "tool_use_behavior", None)
    if isinstance(behavior, str):
        settings["tool_use_behavior"] = behavior
    elif isinstance(behavior, dict):
        settings["tool_use_behavior"] = config_value(behavior)
    elif callable(behavior):
        settings["tool_use_behavior"] = callable_name(behavior)
    reset_tool_choice = getattr(agent, "reset_tool_choice", None)
    if isinstance(reset_tool_choice, bool):
        settings["reset_tool_choice"] = reset_tool_choice
    return {"agent_settings": settings}
