import json
from typing import Any
from typing import Dict
from typing import List
from typing import Optional
from typing import Union

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
from ddtrace.llmobs._integrations.utils import LLMObsTraceInfo
from ddtrace.llmobs._integrations.utils import OaiSpanAdapter
from ddtrace.llmobs._integrations.utils import OaiTraceAdapter
from ddtrace.llmobs._integrations.utils import ToolCallTracker
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
    ) -> Optional[Span]:
        oai_trace = kwargs.get("oai_trace")
        if not isinstance(oai_trace, OaiTraceAdapter):
            logger.warning("Expected OaiTraceAdapter but got %s", type(oai_trace))
            return None

        llmobs_span = super().trace(
            pin,
            operation_id=oai_trace.name,
            submit_to_llmobs=True,
            span_name=oai_trace.name,
        )

        llmobs_span._set_ctx_item(SPAN_KIND, "workflow")
        self.oai_to_llmobs_span[oai_trace.trace_id] = llmobs_span

        self.llmobs_traces[format_trace_id(llmobs_span.trace_id)] = LLMObsTraceInfo(
            span_id=str(llmobs_span.span_id),
            trace_id=format_trace_id(llmobs_span.trace_id),
        )

        group_id = oai_trace.group_id
        if group_id:
            llmobs_span._set_ctx_item(SESSION_ID, group_id)

        return llmobs_span

    def start_span_from_oai_span(
        self,
        pin: Pin,
        **kwargs,
    ) -> Optional[Span]:
        oai_span = kwargs.get("oai_span")
        if not oai_span or not isinstance(oai_span, OaiSpanAdapter):
            return None

        span_kind = oai_span.llmobs_span_kind
        if not span_kind:
            return None

        llmobs_span = super().trace(pin, operation_id=oai_span.name, submit_to_llmobs=True, span_name=oai_span.name)

        llmobs_span._set_ctx_item(SPAN_KIND, span_kind)
        self.oai_to_llmobs_span[oai_span.span_id] = llmobs_span

        # special handling for llm span naming
        if span_kind == "llm":
            parent = _get_nearest_llmobs_ancestor(llmobs_span)
            if parent and parent._get_ctx_item(SPAN_KIND) == "agent" and _get_span_name(parent):
                llmobs_span._set_ctx_item(NAME, _get_span_name(parent) + " (LLM)")

        trace_info = self.get_trace_info(oai_span)

        # keep tracking the current active agent for a trace
        if trace_info and span_kind == "agent" and llmobs_span._get_ctx_item(PARENT_ID_KEY) == trace_info.span_id:
            trace_info.current_top_level_agent_span_id = str(llmobs_span.span_id)

        # special handling for the first llm span of a trace which holds the input value for
        # the entire trace
        if trace_info and span_kind == "llm" and not trace_info.input_oai_span:
            trace_info.input_oai_span = oai_span
            add_span_link(
                llmobs_span,
                trace_info.span_id,
                trace_info.trace_id,
                "input",
                "input",
            )

        return llmobs_span

    def _set_trace_attributes(self, span: Span, oai_trace: OaiTraceAdapter) -> None:
        trace_info = self.get_trace_info(oai_trace)
        if not trace_info:
            return

        if trace_info.input_oai_span:
            input_value = trace_info.input_oai_span.llmobs_trace_input()
            if input_value:
                span._set_ctx_item(INPUT_VALUE, input_value)

        if trace_info.output_oai_span:
            output_oai_span = trace_info.output_oai_span
            if output_oai_span:
                span._set_ctx_item(OUTPUT_VALUE, output_oai_span.response_output_text)
                llmobs_output_span = self.oai_to_llmobs_span[output_oai_span.span_id]
                add_span_link(
                    span,
                    str(llmobs_output_span.span_id),
                    format_trace_id(llmobs_output_span.trace_id),
                    "output",
                    "output",
                )

        metadata = oai_trace.metadata
        if metadata:
            span._set_ctx_item(METADATA, metadata)

    def _llmobs_set_tags(
        self,
        span: Span,
        args: List[Any],
        kwargs: Dict[str, Union[Any, OaiTraceAdapter, OaiSpanAdapter]],
        response: Optional[Any] = None,
        operation: str = "",
    ) -> None:
        """Sets meta tags and metrics for span events to be sent to LLMObs."""
        oai_trace = kwargs.get("oai_trace")
        if oai_trace is not None and isinstance(oai_trace, OaiTraceAdapter):
            self._set_trace_attributes(span, oai_trace)
            return

        oai_span = kwargs.get("oai_span")
        if not oai_span:
            return

        if not isinstance(oai_span, OaiSpanAdapter):
            logger.warning("Expected OaiSpanAdapter but got %s", type(oai_span))
            return

        span_type = oai_span.span_type

        if oai_span.error:
            error_msg = oai_span.get_error_message()
            error_data = oai_span.get_error_data()
            span.error = 1
            if error_msg:
                span.set_tag("error.type", error_msg)
            if error_data and error_msg:
                span.set_tag("error.message", error_msg + "\n" + json.dumps(error_data))

        if span_type == "response":
            self._set_response_attributes(span, oai_span)
            self.update_trace_info_output_span(oai_span)
        elif span_type in ["function", "tool"]:
            self._set_tool_attributes(span, oai_span)
        elif span_type == "handoff":
            self._set_handoff_attributes(span, oai_span)
        elif span_type == "agent":
            self._set_agent_attributes(span, oai_span)
        elif span_type == "custom":
            custom_data = oai_span.formatted_custom_data
            if custom_data:
                span._set_ctx_item(METADATA, custom_data)

    def _set_response_attributes(self, span: Span, oai_span: OaiSpanAdapter) -> None:
        """Sets attributes for response type spans."""
        if not oai_span.response:
            return

        # Set model information
        if oai_span.llmobs_model_name:
            span._set_ctx_item(MODEL_NAME, oai_span.llmobs_model_name)
            span._set_ctx_item(MODEL_PROVIDER, "openai")

        # Process input messages
        if oai_span.input:
            messages, tool_call_ids = oai_span.llmobs_input_messages()

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

        # Process output messages
        if oai_span.response and oai_span.response.output:
            messages, tool_call_outputs = oai_span.llmobs_output_messages()
            for tool_id, tool_name, tool_args in tool_call_outputs:
                self.tool_tracker.register_llm_tool_call(
                    tool_id=tool_id,
                    tool_name=tool_name,
                    arguments=tool_args,
                    llm_span_id=str(span.span_id),
                )

            span._set_ctx_item(OUTPUT_MESSAGES, messages)

        # Set metadata and metrics
        metadata = oai_span.llmobs_metadata
        if metadata:
            span._set_ctx_item(METADATA, metadata)

        metrics = oai_span.llmobs_metrics
        if metrics:
            span._set_ctx_item(METRICS, metrics)

    def _set_tool_attributes(self, span: Span, oai_span: OaiSpanAdapter) -> None:
        """Sets attributes for function/tool type spans."""
        span._set_ctx_item(INPUT_VALUE, oai_span.input or "")
        span._set_ctx_item(OUTPUT_VALUE, oai_span.output or "")

        tool_call = self.tool_tracker.find_by_input(oai_span.name, oai_span.input)
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
            self.tool_tracker.cleanup_input(oai_span.name, oai_span.input)

    def _set_handoff_attributes(self, span: Span, oai_span: OaiSpanAdapter) -> None:
        """Sets attributes for handoff type spans."""
        handoff_tool_name = "transfer_to_{}".format("_".join(oai_span.to_agent.split(" ")).lower())
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

        span._set_ctx_item("input_value", oai_span.from_agent or "")
        span._set_ctx_item("output_value", oai_span.to_agent or "")

    def _set_agent_attributes(self, span: Span, oai_span: OaiSpanAdapter) -> None:
        """Sets attributes for agent type spans."""
        # Use the adapter's metadata property
        if oai_span.llmobs_metadata:
            span._set_ctx_item(METADATA, oai_span.llmobs_metadata)

    def get_trace_info(self, oai_trace_or_span) -> Optional[LLMObsTraceInfo]:
        """Get trace info for a span safely.

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

    def update_trace_info_output_span(self, oai_span: OaiSpanAdapter) -> None:
        """Update trace info with output span.

        Args:
            oai_span: The span adapter being processed.
        """
        trace_info = self.get_trace_info(oai_span)
        if not trace_info:
            return

        llmobs_span = self.oai_to_llmobs_span.get(oai_span.span_id)
        if not llmobs_span:
            return

        current_top_level_agent_span_id = trace_info.current_top_level_agent_span_id
        if (
            current_top_level_agent_span_id
            and llmobs_span._get_ctx_item(PARENT_ID_KEY) == current_top_level_agent_span_id
        ):
            trace_info.output_oai_span = oai_span

    def clear_state(self) -> None:
        self.oai_to_llmobs_span.clear()
        self.llmobs_traces.clear()
        self.tool_tracker = ToolCallTracker()
