import json
from typing import Any
from typing import Optional
from typing import Union
import weakref

from ddtrace.internal import core
from ddtrace.internal.logger import get_logger
from ddtrace.internal.utils import get_argument_value
from ddtrace.internal.utils.formats import format_trace_id
from ddtrace.llmobs._constants import DISPATCH_ON_GUARDRAIL_SPAN_START
from ddtrace.llmobs._constants import DISPATCH_ON_LLM_TOOL_CHOICE
from ddtrace.llmobs._constants import DISPATCH_ON_OPENAI_AGENT_SPAN_FINISH
from ddtrace.llmobs._constants import DISPATCH_ON_TOOL_CALL
from ddtrace.llmobs._constants import DISPATCH_ON_TOOL_CALL_OUTPUT_USED
from ddtrace.llmobs._constants import OAI_HANDOFF_TOOL_ARG
from ddtrace.llmobs._constants import ROOT_PARENT_ID
from ddtrace.llmobs._integrations.base import BaseLLMIntegration
from ddtrace.llmobs._integrations.utils import CONTEXT_SECTION_ASSISTANT_MESSAGES
from ddtrace.llmobs._integrations.utils import CONTEXT_SECTION_SYSTEM
from ddtrace.llmobs._integrations.utils import CONTEXT_SECTION_TOOLS
from ddtrace.llmobs._integrations.utils import CONTEXT_SECTION_USER_MESSAGES
from ddtrace.llmobs._integrations.utils import LLMObsTraceInfo
from ddtrace.llmobs._integrations.utils import OaiSpanAdapter
from ddtrace.llmobs._integrations.utils import OaiTraceAdapter
from ddtrace.llmobs._integrations.utils import split_tokens_by_chars
from ddtrace.llmobs._integrations.utils import tag_context_delta
from ddtrace.llmobs._utils import _annotate_llmobs_span_data
from ddtrace.llmobs._utils import _get_nearest_llmobs_ancestor
from ddtrace.llmobs._utils import get_llmobs_parent_id
from ddtrace.llmobs._utils import get_llmobs_span_name
from ddtrace.llmobs._utils import get_tool_version_from_llm_span
from ddtrace.llmobs._utils import load_data_value
from ddtrace.llmobs._utils import safe_json
from ddtrace.trace import Span


logger = get_logger(__name__)


def count_tools_chars(agent) -> int:
    """Sum character counts for the agent's tools + handoffs serialized representations.

    AIDEV-NOTE: MLOB-7584 — handoffs are folded into the ``tools`` category because the
    context_delta payload has no dedicated handoffs key. MCP server tools (``agent.mcp_servers``)
    are intentionally NOT counted here: their definitions are fetched asynchronously at runtime
    via ``agent.get_mcp_tools()`` and including them would require a separate async-aware
    accounting path. Agents that rely heavily on MCP server tools will see their ``tools``
    category under-counted in the resulting context_delta. A follow-up slice can capture
    MCP-tools tokens once there is signal that it matters.
    """
    chars = 0
    for tool in getattr(agent, "tools", None) or []:
        chars += len(safe_json(tool) or str(tool))
    for handoff in getattr(agent, "handoffs", None) or []:
        chars += len(safe_json(handoff) or str(handoff))
    return chars


def split_message_chars(messages: Any) -> tuple[int, int]:
    """Return ``(user_chars, assistant_chars)`` for a response-span's ``input`` payload.

    AIDEV-NOTE: MLOB-7584 — ``input`` is typed as ``str | list[Any]``. When the initial
    user prompt is the only input it arrives as a string and is bucketed into ``user``.
    When the agent loop has run for >=1 turn, the message list contains role-tagged
    dicts/objects with roles ``user``, ``assistant``, ``system``, and ``tool``.

    Bucketing rules:
    - ``user`` -> user_chars
    - ``assistant`` and ``tool`` -> assistant_chars. ``tool`` (tool-result) messages are
      injected into the input after each tool call by the agent loop. Bucketing them with
      ``assistant`` keeps the agent-produced side of the conversation cohesive AND ensures
      total counted chars equals total response.input chars, so the char-proportional
      token split stays accurate on multi-turn tool-using runs.
    - ``system`` is excluded here; system tokens flow through ``response_system_instructions``
      directly into the ``system`` category.
    """
    if isinstance(messages, str):
        return len(messages), 0
    if not isinstance(messages, list):
        return 0, 0
    user_chars = 0
    assistant_chars = 0
    for msg in messages:
        role = msg.get("role") if isinstance(msg, dict) else getattr(msg, "role", None)
        content = msg.get("content") if isinstance(msg, dict) else getattr(msg, "content", None)
        chars = len(str(content)) if content is not None else 0
        if role == "user":
            user_chars += chars
        elif role in ("assistant", "tool"):
            assistant_chars += chars
    return user_chars, assistant_chars


class OpenAIAgentsIntegration(BaseLLMIntegration):
    _integration_name = "openai_agents"

    # AIDEV-NOTE: MLOB-7584 — OpenAI doesn't expose context_window via the API response, so
    # maintain a static map here; update on new model launches. The 0 fallback matches the
    # Claude integration's behavior when its /context output cannot parse a window size.
    # Lift to a shared registry only when a second integration requires the same mapping.
    # Entry ordering does not matter at lookup time — the resolver sorts keys by length
    # descending so longer prefixes (e.g. "gpt-4o-mini", "o1-preview") win over shorter
    # ones (e.g. "gpt-4o", "o1").
    _OPENAI_MODEL_CONTEXT_WINDOWS: dict[str, int] = {
        "gpt-4o-mini": 128_000,
        "gpt-4o": 128_000,
        "gpt-4-turbo": 128_000,
        "gpt-4.1": 1_047_576,
        "gpt-4": 8_192,
        "gpt-3.5-turbo": 16_385,
        "gpt-5-mini": 400_000,
        "gpt-5": 400_000,
        "o1-preview": 128_000,
        "o1-mini": 128_000,
        "o1": 200_000,
        "o3-mini": 200_000,
        "o3": 200_000,
        "o4-mini": 200_000,
    }

    def __init__(self, integration_config: Any) -> None:
        super().__init__(integration_config)
        # a map of openai span ids to the corresponding llm obs span
        self.oai_to_llmobs_span: weakref.WeakValueDictionary[str, Span] = weakref.WeakValueDictionary()
        # a map of LLM Obs trace ids to LLMObsTraceInfo which stores metadata about the trace
        # used to set attributes on the root span of the trace.
        self.llmobs_traces: dict[str, LLMObsTraceInfo] = {}
        # MLOB-7584: per-trace context snapshot state (keyed by ddtrace trace_id).
        # Populated by record_llm_side / record_agent_side; consumed by emit_context_delta.
        self._context_state: dict[int, dict[str, Any]] = {}

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
        self._context_state.clear()

    def _context_window_for(self, model: str) -> int:
        """Look up the model's context window. Returns 0 if unknown."""
        if not model:
            return 0
        if model in self._OPENAI_MODEL_CONTEXT_WINDOWS:
            return self._OPENAI_MODEL_CONTEXT_WINDOWS[model]
        # Prefix match for dated model names (e.g. "gpt-4o-2024-08-06" -> "gpt-4o").
        # Iterate longest-prefix-first so "gpt-4o-mini" wins over "gpt-4o" / "gpt-4".
        for prefix in sorted(self._OPENAI_MODEL_CONTEXT_WINDOWS, key=len, reverse=True):
            if model.startswith(prefix):
                return self._OPENAI_MODEL_CONTEXT_WINDOWS[prefix]
        return 0

    def record_llm_side(
        self,
        trace_id: int,
        *,
        input_tokens: int,
        system_chars: int,
        user_chars: int,
        assistant_chars: int,
        model: str,
    ) -> None:
        """MLOB-7584 — hook A. Capture per-LLM-call context categories from a response span.

        Called from ``LLMObsTraceProcessor.on_span_end`` when ``span_type == "response"``.
        First call locks the ``first_llm`` snapshot; every subsequent call overwrites ``last_llm``.

        AIDEV-NOTE: MLOB-7584 — model is saved on both ``first_llm`` and ``last_llm`` snapshots,
        but the emitted payload contains a single ``context_window_size`` field (matching the
        Claude integration's contract — see _parse_context_delta in claude_agent_sdk.py). The
        resolver picks the last-turn model's window for both usage_pct fields. For multi-model
        handoff traces this may under/overstate the first-turn percentage; the contract does
        not currently support per-snapshot windows.
        """
        if not self.llmobs_enabled:
            return
        snapshot = {
            "input_tokens": input_tokens,
            "system_chars": system_chars,
            "user_chars": user_chars,
            "assistant_chars": assistant_chars,
            "model": model or "",
        }
        state = self._context_state.setdefault(trace_id, {})
        if "first_llm" not in state:
            state["first_llm"] = snapshot
        state["last_llm"] = snapshot

    def record_agent_side(self, trace_id: int, *, tools_chars: int) -> None:
        """MLOB-7584 — hook B. Capture per-turn agent-side category (tools + handoffs).

        Called from ``_patched_run_single_turn`` after the awaited function returns.
        First call locks the ``first_agent`` snapshot; every subsequent call overwrites ``last_agent``.
        """
        if not self.llmobs_enabled:
            return
        snapshot = {"tools_chars": tools_chars}
        state = self._context_state.setdefault(trace_id, {})
        if "first_agent" not in state:
            state["first_agent"] = snapshot
        state["last_agent"] = snapshot

    def emit_context_delta(self, trace_root_span: Span, trace_id: int) -> None:
        """MLOB-7584 — hook C. Emit the assembled context_delta on the agent-kind root span.

        Called from ``LLMObsTraceProcessor.on_trace_end`` just before the root span finishes.
        No-ops if neither LLM snapshot was captured (degenerate trace).
        """
        state = self._context_state.pop(trace_id, None)
        if not state:
            return
        first_llm = state.get("first_llm")
        last_llm = state.get("last_llm")
        if not first_llm or not last_llm:
            return

        first_agent = state.get("first_agent", {"tools_chars": 0})
        last_agent = state.get("last_agent", {"tools_chars": 0})

        first_chars = {
            CONTEXT_SECTION_SYSTEM: first_llm["system_chars"],
            CONTEXT_SECTION_TOOLS: first_agent["tools_chars"],
            CONTEXT_SECTION_USER_MESSAGES: first_llm["user_chars"],
            CONTEXT_SECTION_ASSISTANT_MESSAGES: first_llm["assistant_chars"],
        }
        last_chars = {
            CONTEXT_SECTION_SYSTEM: last_llm["system_chars"],
            CONTEXT_SECTION_TOOLS: last_agent["tools_chars"],
            CONTEXT_SECTION_USER_MESSAGES: last_llm["user_chars"],
            CONTEXT_SECTION_ASSISTANT_MESSAGES: last_llm["assistant_chars"],
        }

        first_token_counts = split_tokens_by_chars(first_llm["input_tokens"], first_chars)
        last_token_counts = split_tokens_by_chars(last_llm["input_tokens"], last_chars)

        tag_context_delta(
            trace_root_span,
            first_token_counts=first_token_counts,
            last_token_counts=last_token_counts,
            first_input_tokens=first_llm["input_tokens"],
            last_input_tokens=last_llm["input_tokens"],
            context_window_size=self._context_window_for(last_llm.get("model", "")),
        )

    def tag_agent_manifest(self, span: Span, args: list[Any], kwargs: dict[str, Any], agent_index: int) -> None:
        agent = get_argument_value(args, kwargs, agent_index, "agent", True)
        if not agent or not self.llmobs_enabled:
            return

        manifest = {}
        manifest["framework"] = "OpenAI"
        if hasattr(agent, "name"):
            manifest["name"] = agent.name
        if hasattr(agent, "instructions"):
            manifest["instructions"] = agent.instructions
        if hasattr(agent, "handoff_description"):
            manifest["handoff_description"] = agent.handoff_description
        if hasattr(agent, "model"):
            model = agent.model
            manifest["model"] = model if isinstance(model, str) else getattr(model, "model", "")

        model_settings = self._extract_model_settings_from_agent(agent)
        if model_settings:
            manifest["model_settings"] = model_settings

        tools = self._extract_tools_from_agent(agent)
        if tools:
            manifest["tools"] = tools

        handoffs = self._extract_handoffs_from_agent(agent)
        if handoffs:
            manifest["handoffs"] = handoffs

        guardrails = self._extract_guardrails_from_agent(agent)
        if guardrails:
            manifest["guardrails"] = guardrails

        _annotate_llmobs_span_data(span, agent_manifest=manifest)

    def _extract_model_settings_from_agent(self, agent):
        if not hasattr(agent, "model_settings"):
            return None

        # convert model_settings to dict if it's not already
        model_settings = agent.model_settings
        if type(model_settings) != dict:
            model_settings = getattr(model_settings, "__dict__", None)

        return load_data_value(model_settings)

    def _extract_tools_from_agent(self, agent):
        if not hasattr(agent, "tools") or not agent.tools:
            return None

        tools = []
        for tool in agent.tools:
            tool_dict = {}
            tool_name = getattr(tool, "name", None)
            if tool_name:
                tool_dict["name"] = tool_name
            if tool_name == "web_search_preview":
                if hasattr(tool, "user_location"):
                    tool_dict["user_location"] = tool.user_location
                if hasattr(tool, "search_context_size"):
                    tool_dict["search_context_size"] = tool.search_context_size
            elif tool_name == "file_search":
                if hasattr(tool, "vector_store_ids"):
                    tool_dict["vector_store_ids"] = tool.vector_store_ids
                if hasattr(tool, "max_num_results"):
                    tool_dict["max_num_results"] = tool.max_num_results
                if hasattr(tool, "include_search_results"):
                    tool_dict["include_search_results"] = tool.include_search_results
                if hasattr(tool, "ranking_options"):
                    tool_dict["ranking_options"] = tool.ranking_options
                if hasattr(tool, "filters"):
                    tool_dict["filters"] = tool.filters
            elif tool_name == "computer_use_preview":
                if hasattr(tool, "computer"):
                    tool_dict["computer"] = tool.computer
                if hasattr(tool, "on_safety_check"):
                    tool_dict["on_safety_check"] = tool.on_safety_check
            elif tool_name == "code_interpreter":
                if hasattr(tool, "tool_config"):
                    tool_dict["tool_config"] = tool.tool_config
            elif tool_name == "hosted_mcp":
                if hasattr(tool, "tool_config"):
                    tool_dict["tool_config"] = tool.tool_config
                if hasattr(tool, "on_approval_request"):
                    tool_dict["on_approval_request"] = tool.on_approval_request
            elif tool_name == "image_generation":
                if hasattr(tool, "tool_config"):
                    tool_dict["tool_config"] = tool.tool_config
            elif tool_name == "local_shell":
                if hasattr(tool, "executor"):
                    tool_dict["executor"] = tool.executor
            else:
                if hasattr(tool, "description"):
                    tool_dict["description"] = tool.description
                if hasattr(tool, "strict_json_schema"):
                    tool_dict["strict_json_schema"] = tool.strict_json_schema
                if hasattr(tool, "params_json_schema"):
                    parameter_schema = tool.params_json_schema
                    required_params = {param: True for param in parameter_schema.get("required", [])}
                    parameters = {}
                    for param, schema in parameter_schema.get("properties", {}).items():
                        param_dict = {}
                        if "type" in schema:
                            param_dict["type"] = schema["type"]
                        if "title" in schema:
                            param_dict["title"] = schema["title"]
                        if param in required_params:
                            param_dict["required"] = True
                        parameters[param] = param_dict
                    tool_dict["parameters"] = parameters
            tools.append(tool_dict)

        return tools

    def _extract_handoffs_from_agent(self, agent):
        if not hasattr(agent, "handoffs") or not agent.handoffs:
            return None

        handoffs = []
        for handoff in agent.handoffs:
            handoff_dict = {}
            if hasattr(handoff, "handoff_description") or hasattr(handoff, "tool_description"):
                handoff_dict["handoff_description"] = getattr(handoff, "handoff_description", None) or getattr(
                    handoff, "tool_description", None
                )
            if hasattr(handoff, "name") or hasattr(handoff, "agent_name"):
                handoff_dict["agent_name"] = getattr(handoff, "name", None) or getattr(handoff, "agent_name", None)
            if hasattr(handoff, "tool_name"):
                handoff_dict["tool_name"] = handoff.tool_name
            if handoff_dict:
                handoffs.append(handoff_dict)

        return handoffs

    def _extract_guardrails_from_agent(self, agent):
        guardrails = []
        if hasattr(agent, "input_guardrails"):
            guardrails.extend([getattr(guardrail, "name", "") for guardrail in agent.input_guardrails])
        if hasattr(agent, "output_guardrails"):
            guardrails.extend([getattr(guardrail, "name", "") for guardrail in agent.output_guardrails])
        return guardrails
