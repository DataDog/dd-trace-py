import json
from typing import Any
from typing import Dict
from typing import List
from typing import Optional
from typing import Union
import weakref

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
from ddtrace.llmobs._utils import _get_nearest_llmobs_ancestor
from ddtrace.llmobs._utils import _get_span_name
from ddtrace.trace import Pin
from ddtrace.trace import Span


logger = get_logger(__name__)


class OpenAIAgentsIntegration(BaseLLMIntegration):
    _integration_name = "openai_agents"

    def __init__(self, integration_config):
        super().__init__(integration_config)
        # a map of openai span ids to the corresponding llm obs span
        self.oai_to_llmobs_span: Dict[str, Span] = weakref.WeakValueDictionary()
        # a map of LLM Obs trace ids to LLMObsTraceInfo which stores metadata about the trace
        # used to set attributes on the root span of the trace.
        self.llmobs_traces: Dict[str, LLMObsTraceInfo] = {}

    def trace(
        self,
        pin: Pin,
        operation_id: str = "",
        submit_to_llmobs: bool = False,
        **kwargs,
    ) -> Span:
        oai_trace = kwargs.get("oai_trace")
        oai_span = kwargs.get("oai_span")

        span_name = oai_trace.name if oai_trace else oai_span.name if oai_span else "openai_agents.request"

        llmobs_span = super().trace(
            pin,
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
        return llmobs_span

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
        span._set_ctx_item(SPAN_KIND, span_kind)

        if oai_span.error:
            error_msg = oai_span.get_error_message()
            error_data = oai_span.get_error_data()
            span.error = 1
            if error_msg:
                span.set_tag("error.type", json.dumps(error_data))
            if error_data and error_msg:
                span.set_tag("error.message", error_msg)

        if span_type == "response":
            self._llmobs_set_response_attributes(span, oai_span)
            self._llmobs_update_trace_info_output(oai_span)
        elif span_type in ("function", "tool"):
            self._llmobs_set_tool_attributes(span, oai_span)
        elif span_type == "handoff":
            self._llmobs_set_handoff_attributes(span, oai_span)
        elif span_type == "agent":
            self._llmobs_set_agent_attributes(span, oai_span)
        elif span_type == "custom":
            custom_data = oai_span.formatted_custom_data
            if custom_data:
                span._set_ctx_item(METADATA, custom_data)

    def _llmobs_update_trace_info_input(self, oai_span: OaiSpanAdapter, llmobs_span: Span) -> None:
        """
        Store the openai span we are using as the input to the top level trace. Since the openai trace
        itself does not have input / output data, we use the input to the first LLM call of the first
        agent invocation as the top level trace input.
        """
        trace_info = self._llmobs_get_trace_info(oai_span)
        if not trace_info:
            return
        if oai_span.span_type == "agent" and llmobs_span._get_ctx_item(PARENT_ID_KEY) == trace_info.span_id:
            trace_info.current_top_level_agent_span_id = str(llmobs_span.span_id)
        if (
            oai_span.span_type == "response"
            and not trace_info.input_oai_span
            and llmobs_span._get_ctx_item(PARENT_ID_KEY) == trace_info.current_top_level_agent_span_id
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
        if (
            current_top_level_agent_span_id
            and llmobs_span._get_ctx_item(PARENT_ID_KEY) == current_top_level_agent_span_id
        ):
            trace_info.output_oai_span = oai_span

    def _llmobs_set_trace_attributes(self, span: Span, oai_trace: OaiTraceAdapter) -> None:
        trace_info = self._llmobs_get_trace_info(oai_trace)
        if not trace_info:
            return

        group_id = oai_trace.group_id
        if group_id:
            span._set_ctx_item(SESSION_ID, group_id)

        span._set_ctx_item(SPAN_KIND, "workflow")

        if trace_info.input_oai_span:
            input_value = trace_info.input_oai_span.llmobs_trace_input()
            if input_value:
                span._set_ctx_item(INPUT_VALUE, input_value)

        if trace_info.output_oai_span:
            output_value = trace_info.output_oai_span.response_output_text
            if output_value:
                span._set_ctx_item(OUTPUT_VALUE, output_value)

        metadata = oai_trace.metadata
        if metadata:
            span._set_ctx_item(METADATA, metadata)

    def _llmobs_set_response_attributes(self, span: Span, oai_span: OaiSpanAdapter) -> None:
        """Sets attributes for response type spans."""
        if not oai_span.response:
            return

        parent = _get_nearest_llmobs_ancestor(span)
        trace_info = self._llmobs_get_trace_info(oai_span)
        if parent and trace_info and span._get_ctx_item(PARENT_ID_KEY) == trace_info.current_top_level_agent_span_id:
            span._set_ctx_item(NAME, _get_span_name(parent) + " (LLM)")

        if oai_span.llmobs_model_name:
            span._set_ctx_item(MODEL_NAME, oai_span.llmobs_model_name)
            span._set_ctx_item(MODEL_PROVIDER, "openai")

        if oai_span.input:
            messages = oai_span.llmobs_input_messages()
            span._set_ctx_item(INPUT_MESSAGES, messages)

        if oai_span.response and oai_span.response.output:
            messages = oai_span.llmobs_output_messages()
            span._set_ctx_item(OUTPUT_MESSAGES, messages)

        metadata = oai_span.llmobs_metadata
        if metadata:
            span._set_ctx_item(METADATA, metadata)

        metrics = oai_span.llmobs_metrics
        if metrics:
            span._set_ctx_item(METRICS, metrics)

    def _llmobs_set_tool_attributes(self, span: Span, oai_span: OaiSpanAdapter) -> None:
        span._set_ctx_item(INPUT_VALUE, oai_span.input or "")
        span._set_ctx_item(OUTPUT_VALUE, oai_span.output or "")

    def _llmobs_set_handoff_attributes(self, span: Span, oai_span: OaiSpanAdapter) -> None:
        handoff_tool_name = "transfer_to_{}".format("_".join(oai_span.to_agent.split(" ")).lower())
        span.name = handoff_tool_name
        span._set_ctx_item("input_value", oai_span.from_agent or "")
        span._set_ctx_item("output_value", oai_span.to_agent or "")

    def _llmobs_set_agent_attributes(self, span: Span, oai_span: OaiSpanAdapter) -> None:
        if oai_span.llmobs_metadata:
            span._set_ctx_item(METADATA, oai_span.llmobs_metadata)

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
