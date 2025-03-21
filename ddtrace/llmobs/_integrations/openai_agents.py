from typing import Any
from typing import Dict
from typing import List
from typing import Optional

from ddtrace.contrib.internal.openai_agents.utils import LLMObsTraceInfo
from ddtrace.contrib.internal.openai_agents.utils import ToolCallTracker
from ddtrace.contrib.internal.openai_agents.utils import _determine_span_kind
from ddtrace.contrib.internal.openai_agents.utils import _process_input_messages
from ddtrace.contrib.internal.openai_agents.utils import _process_output_messages
from ddtrace.contrib.internal.openai_agents.utils import load_span_data_value
from ddtrace.contrib.internal.openai_agents.utils import set_error_on_span
from ddtrace.contrib.internal.openai_agents.utils import trace_input_from_response_span
from ddtrace.internal.logger import get_logger
from ddtrace.internal.utils.formats import format_trace_id
from ddtrace.llmobs._constants import INPUT_MESSAGES
from ddtrace.llmobs._constants import INPUT_VALUE
from ddtrace.llmobs._constants import METADATA
from ddtrace.llmobs._constants import METRICS
from ddtrace.llmobs._constants import MODEL_NAME
from ddtrace.llmobs._constants import MODEL_PROVIDER
from ddtrace.llmobs._constants import NAME
from ddtrace.llmobs._constants import OUTPUT_MESSAGES
from ddtrace.llmobs._constants import OUTPUT_VALUE
from ddtrace.llmobs._constants import PARENT_ID_KEY
from ddtrace.llmobs._constants import SESSION_ID
from ddtrace.llmobs._constants import SPAN_KIND
from ddtrace.llmobs._integrations.base import BaseLLMIntegration
from ddtrace.llmobs._utils import _get_nearest_llmobs_ancestor
from ddtrace.llmobs._utils import _get_span_name
from ddtrace.llmobs._utils import add_span_link
from ddtrace.trace import Pin
from ddtrace.trace import Span


logger = get_logger(__name__)


class OpenAIAgentsIntegration(BaseLLMIntegration):
    _integration_name = "openai_agents"

    def __init__(self, integration_config):
        super().__init__(integration_config)
        self.oai_to_llmobs_span = {}  # type: dict[str, Span]
        self.llmobs_traces = {}  # type: dict[str, LLMObsTraceInfo]
        self.tool_tracker = ToolCallTracker()

    def start_span_from_oai_trace(
        self,
        pin: Pin,
        **kwargs,
    ) -> Span:
        raw_oai_trace = kwargs.get("raw_oai_trace")
        span_name = getattr(raw_oai_trace, "name", "Agent workflow")
        llmobs_span = super().trace(
            pin,
            operation_id=span_name,
            submit_to_llmobs=True,
            span_name=span_name,
        )

        llmobs_span._set_ctx_item(SPAN_KIND, "workflow")
        self.oai_to_llmobs_span[getattr(raw_oai_trace, "trace_id")] = llmobs_span

        self.llmobs_traces[format_trace_id(llmobs_span.trace_id)] = LLMObsTraceInfo(
            span_id=str(llmobs_span.span_id),
            trace_id=format_trace_id(llmobs_span.trace_id),
        )

        if raw_oai_trace and raw_oai_trace.export().get("group_id"):
            llmobs_span._set_ctx_item(SESSION_ID, raw_oai_trace.export().get("group_id"))

        return llmobs_span

    def start_span_from_oai_span(
        self,
        pin: Pin,
        **kwargs,
    ) -> Optional[Span]:
        raw_oai_span = kwargs.get("raw_oai_span")
        if not raw_oai_span:
            return None

        span_name = raw_oai_span.export().get("span_data", {}).get("name")
        span_kind = _determine_span_kind(raw_oai_span.export().get("span_data", {}).get("type"))

        span_name = span_name if span_name else "openai_agents.{}".format(span_kind.lower())

        llmobs_span = super().trace(pin, operation_id=span_name, submit_to_llmobs=True, span_name=span_name)

        llmobs_span._set_ctx_item(SPAN_KIND, span_kind)
        self.oai_to_llmobs_span[raw_oai_span.span_id] = llmobs_span

        # special handling for llm span naming
        if span_kind == "llm":
            parent = _get_nearest_llmobs_ancestor(llmobs_span)
            if parent and parent._get_ctx_item(SPAN_KIND) == "agent" and _get_span_name(parent):
                llmobs_span._set_ctx_item(NAME, _get_span_name(parent) + " (LLM)")

        trace_info = self.get_trace_info(raw_oai_span)

        # keep tracking the current active agent for a trace
        if trace_info and span_kind == "agent" and llmobs_span._get_ctx_item(PARENT_ID_KEY) == trace_info.span_id:
            trace_info.current_top_level_agent_span_id = str(llmobs_span.span_id)

        # special handling for the first llm span of a trace which holds the input value for
        # the entire trace
        if trace_info and span_kind == "llm" and not trace_info.input_oai_span:
            trace_info.input_oai_span = raw_oai_span
            add_span_link(
                llmobs_span,
                trace_info.span_id,
                trace_info.trace_id,
                "input",
                "input",
            )

        return llmobs_span

    def _llmobs_set_tags(
        self,
        span: Span,
        args: List[Any],
        kwargs: Dict[str, Any],
        response: Optional[Any] = None,
        operation: str = "",
    ) -> None:
        """Sets meta tags and metrics for span events to be sent to LLMObs."""
        if kwargs.get("raw_oai_trace"):
            self._set_trace_attributes(span, kwargs.get("raw_oai_trace"))
            return

        raw_oai_span = kwargs.get("raw_oai_span")
        if not raw_oai_span or not hasattr(raw_oai_span, "span_data"):
            return

        span_data = raw_oai_span.span_data

        span_type = getattr(span_data, "type", None)
        set_error_on_span(span, raw_oai_span)

        if span_type == "response":
            self._set_response_attributes(span, span_data)
            self.update_trace_info_output_span(raw_oai_span)
        elif span_type in ["function", "tool"]:
            self._set_tool_attributes(span, span_data)
        elif span_type == "handoff":
            self._set_handoff_attributes(span, span_data)
        elif span_type == "agent":
            self._set_agent_attributes(span, span_data)
        elif span_type == "generation":
            self._set_generation_attributes(span, span_data)
        elif span_type == "custom":
            custom_data = getattr(span_data, "data", None)
            if custom_data:
                span._set_ctx_item(METADATA, custom_data)

    def _set_trace_attributes(self, span: Span, raw_oai_trace: Any) -> None:
        trace_info = self.get_trace_info(raw_oai_trace)
        if not trace_info:
            return

        if trace_info.input_oai_span and hasattr(trace_info.input_oai_span.span_data, "input"):
            input_value = trace_input_from_response_span(trace_info.input_oai_span)
            if input_value:
                span._set_ctx_item(INPUT_VALUE, input_value)

        if trace_info.output_oai_span:
            span._set_ctx_item(OUTPUT_VALUE, trace_info.output_oai_span.span_data.response.output_text)
            llmobs_output_span = self.oai_to_llmobs_span[trace_info.output_oai_span.span_id]
            add_span_link(
                span,
                str(llmobs_output_span.span_id),
                format_trace_id(llmobs_output_span.trace_id),
                "output",
                "output",
            )

        if raw_oai_trace.metadata:
            span._set_ctx_item(METADATA, raw_oai_trace.metadata)

    def _set_response_attributes(self, span: Span, span_data: Any) -> None:
        """Sets attributes for response type spans."""
        if not hasattr(span_data, "response"):
            return

        if hasattr(span_data.response, "model"):
            span._set_ctx_item(MODEL_NAME, span_data.response.model)
            span._set_ctx_item(MODEL_PROVIDER, "openai")

        if hasattr(span_data, "input"):
            messages, tool_call_ids = _process_input_messages(span_data.input)

            for tool_id in tool_call_ids:
                tool_call = self.tool_tracker.find_by_id(tool_id)
                if not tool_call or not tool_call.tool_span_id or tool_call.is_handoff_completed:
                    continue

                if tool_call.tool_kind == "handoff":
                    self.tool_tracker.mark_handoff_completed(tool_id)

                add_span_link(
                    span,
                    tool_call.tool_span_id,
                    format_trace_id(span.trace_id),
                    "output",
                    "input",
                )

            span._set_ctx_item(INPUT_MESSAGES, messages)

        if hasattr(span_data.response, "output"):
            messages, tool_call_outputs = _process_output_messages(span_data.response.output)
            for tool_id, tool_name, tool_args in tool_call_outputs:
                self.tool_tracker.register_llm_tool_call(
                    tool_id=tool_id,
                    tool_name=tool_name,
                    arguments=tool_args,
                    llm_span_id=str(span.span_id),
                )

            span._set_ctx_item(OUTPUT_MESSAGES, messages)

        metadata = {}
        for field in [
            "temperature",
            "max_output_tokens",
            "top_p",
            "tools",
            "tool_choice",
            "truncation",
        ]:
            if hasattr(span_data.response, field):
                value = getattr(span_data.response, field)
                if value is not None:
                    metadata[field] = load_span_data_value(value)

        if hasattr(span_data.response, "text") and span_data.response.text:
            metadata["text"] = load_span_data_value(span_data.response.text)

        metrics = {}
        if hasattr(span_data.response, "usage"):
            usage = span_data.response.usage
            if hasattr(usage, "input_tokens"):
                metrics["input_tokens"] = usage.input_tokens
            if hasattr(usage, "output_tokens"):
                metrics["output_tokens"] = usage.output_tokens
            if hasattr(usage, "total_tokens"):
                metrics["total_tokens"] = usage.total_tokens
            if hasattr(usage, "output_tokens_details"):
                metadata["reasoning_tokens"] = usage.output_tokens_details.reasoning_tokens

        if metadata:
            span._set_ctx_item(METADATA, metadata)
        if metrics:
            span._set_ctx_item(METRICS, metrics)

    def _set_tool_attributes(self, span: Span, span_data) -> None:
        """Sets attributes for function/tool type spans."""
        span._set_ctx_item(INPUT_VALUE, getattr(span_data, "input", ""))
        output = getattr(span_data, "output", "")
        span._set_ctx_item(OUTPUT_VALUE, output)

        tool_call = self.tool_tracker.find_by_input(span_data.name, span_data.input)
        if tool_call and tool_call.llm_span_id:
            add_span_link(
                span,
                tool_call.llm_span_id,
                format_trace_id(span.trace_id),
                "output",
                "input",
            )

            self.tool_tracker.register_tool_execution(
                tool_id=tool_call.tool_id,
                tool_span_id=str(span.span_id),
                tool_kind="function",
            )
            self.tool_tracker.cleanup_input(span_data.name, span_data.input)

        metadata = {}
        if hasattr(span_data, "function_name"):
            metadata["function_name"] = span_data.function_name
        if hasattr(span_data, "arguments"):
            metadata["arguments"] = span_data.arguments

        if metadata:
            span._set_ctx_item(METADATA, metadata)

    def _set_handoff_attributes(self, span: Span, span_data) -> None:
        """Sets attributes for handoff type spans."""
        handoff_tool_name = "transfer_to_{}".format("_".join(span_data.to_agent.split(" ")).lower())
        span.name = handoff_tool_name

        tool_call = self.tool_tracker.find_by_input(handoff_tool_name, "{}")
        if tool_call and tool_call.llm_span_id:
            add_span_link(
                span,
                tool_call.llm_span_id,
                format_trace_id(span.trace_id),
                "output",
                "input",
            )
            self.tool_tracker.register_tool_execution(
                tool_id=tool_call.tool_id,
                tool_span_id=str(span.span_id),
                tool_kind="handoff",
            )

        span._set_ctx_item("input_value", getattr(span_data, "from_agent", ""))
        span._set_ctx_item("output_value", getattr(span_data, "to_agent", ""))

    def _set_agent_attributes(self, span: Span, span_data) -> None:
        """Sets attributes for agent type spans."""
        metadata = {}
        if hasattr(span_data, "handoffs"):
            metadata["handoffs"] = load_span_data_value(span_data.handoffs)
        if hasattr(span_data, "tools"):
            metadata["tools"] = load_span_data_value(span_data.tools)

        if metadata:
            span._set_ctx_item(METADATA, metadata)

    def _set_generation_attributes(self, span: Span, span_data) -> None:
        """Sets attributes for generation type spans."""
        if hasattr(span_data, "model"):
            span._set_ctx_item(MODEL_NAME, span_data.model)
            span._set_ctx_item(MODEL_PROVIDER, "openai")

        if hasattr(span_data, "input"):
            span._set_ctx_item(INPUT_MESSAGES, span_data.input)

        if hasattr(span_data, "output"):
            span._set_ctx_item(OUTPUT_MESSAGES, span_data.output)

        metadata = {}
        if hasattr(span_data, "model_config") and span_data.model_config:
            metadata.update(load_span_data_value(span_data.model_config))

        metrics = {}
        if hasattr(span_data, "usage") and span_data.usage:
            if "input_tokens" in span_data.usage:
                metrics["input_tokens"] = span_data.usage["input_tokens"]
            if "output_tokens" in span_data.usage:
                metrics["output_tokens"] = span_data.usage["output_tokens"]

        if metadata:
            span._set_ctx_item(METADATA, metadata)
        if metrics:
            span._set_ctx_item(METRICS, metrics)

    def get_trace_info(self, raw_oai_trace_or_span) -> Optional[LLMObsTraceInfo]:
        """Get trace info for a span safely.

        Args:
            raw_oai_span: An openai span to get trace info for.

        Returns:
            The trace info if found, None otherwise.
        """
        key = (
            raw_oai_trace_or_span.span_id
            if hasattr(raw_oai_trace_or_span, "span_id")
            else raw_oai_trace_or_span.trace_id
        )
        llmobs_span = self.oai_to_llmobs_span.get(key)
        if not llmobs_span:
            return None

        return self.llmobs_traces.get(format_trace_id(llmobs_span.trace_id))

    def update_trace_info_output_span(self, raw_oai_span) -> None:
        """Update trace info with output span.

        Args:
            raw_oai_span: The span being processed.
        """
        trace_info = self.get_trace_info(raw_oai_span)
        if not trace_info:
            return

        llmobs_span = self.get_llmobs_span(raw_oai_span.span_id)
        if not llmobs_span:
            return

        current_top_level_agent_span_id = trace_info.current_top_level_agent_span_id
        if (
            current_top_level_agent_span_id
            and llmobs_span._get_ctx_item(PARENT_ID_KEY) == current_top_level_agent_span_id
        ):
            trace_info.output_oai_span = raw_oai_span

    def get_llmobs_span(self, oai_span_id: str) -> Optional[Span]:
        """Get LLMObs span for an OpenAI span ID.

        Args:
            oai_span_id: The OpenAI span ID.

        Returns:
            The LLMObs span if found, None otherwise.
        """
        return self.oai_to_llmobs_span.get(oai_span_id)

    def clear_state(self) -> None:
        self.oai_to_llmobs_span.clear()
        self.llmobs_traces.clear()
        self.tool_tracker = ToolCallTracker()
