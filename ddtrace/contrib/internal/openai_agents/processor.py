from typing import Any

import agents
from agents.tracing.processor_interface import TracingProcessor
from agents.tracing.spans import Span as OaiSpan
from agents.tracing.traces import Trace as OaiTrace

from ddtrace.internal.logger import get_logger
from ddtrace.internal.utils.formats import format_trace_id
from ddtrace.llmobs._integrations.utils import OaiSpanAdapter
from ddtrace.llmobs._integrations.utils import OaiTraceAdapter
from ddtrace.trace import Pin


logger = get_logger(__name__)


class LLMObsTraceProcessor(TracingProcessor):
    def __init__(self, integration):
        super().__init__()
        self._integration = integration

    def on_span_start(self, span: OaiSpan[Any]) -> None:
        """Called when a span starts.

        Args:
            span: The span that started.
        """
        oai_span = OaiSpanAdapter(span)
        if not oai_span.llmobs_span_kind:
            return
        self._integration.trace(Pin.get_from(agents), oai_span=oai_span, submit_to_llmobs=True)

    def on_trace_start(self, trace: OaiTrace) -> None:
        """Called when a trace starts.

        Args:
            trace: The trace that started.
        """
        self._integration.trace(Pin.get_from(agents), oai_trace=OaiTraceAdapter(trace), submit_to_llmobs=True)

    def on_trace_end(self, trace: OaiTrace) -> None:
        """Called when a trace is finished.

        Args:
            trace: The trace that started.
        """
        trace_adapter = OaiTraceAdapter(trace)
        trace_root_span = self._integration.oai_to_llmobs_span.get(trace_adapter.trace_id)
        if not trace_root_span:
            logger.warning("No LLMObs span found for openai trace %s", trace_adapter.trace_id)
            return
        self._integration.llmobs_set_tags(
            trace_root_span,
            [],
            {"oai_trace": trace_adapter},
        )
        self._integration.llmobs_traces.pop(format_trace_id(trace_root_span.trace_id), None)
        trace_root_span.finish()

    def on_span_end(self, span: OaiSpan[Any]) -> None:
        """Called when a span is finished. Should not block or raise exceptions.

        Args:
            span: The span that finished.
        """
        span_adapter = OaiSpanAdapter(span)
        llmobs_span = self._integration.oai_to_llmobs_span.get(span_adapter.span_id)
        if not llmobs_span:
            return
        self._integration.llmobs_set_tags(llmobs_span, [], {"oai_span": span_adapter})
        llmobs_span.finish()

    def force_flush(self) -> None:
        pass

    def shutdown(self) -> None:
        """Shuts down the processor."""
        self._integration.clear_state()


class NoOpTraceProcessor(TracingProcessor):
    def __init__(self):
        super().__init__()

    def on_trace_start(self, trace: OaiTrace) -> None:
        """Called when a trace is started.

        Args:
            trace: The trace that started.
        """
        pass

    def on_trace_end(self, trace: OaiTrace) -> None:
        """Called when a trace is finished.

        Args:
            trace: The trace that started.
        """
        pass

    def on_span_start(self, span: OaiSpan[Any]) -> None:
        """Called when a span is started.

        Args:
            span: The span that started.
        """
        pass

    def on_span_end(self, span: OaiSpan[Any]) -> None:
        """Called when a span is finished. Should not block or raise exceptions.

        Args:
            span: The span that finished.
        """
        pass

    def shutdown(self) -> None:
        pass

    def force_flush(self) -> None:
        pass
