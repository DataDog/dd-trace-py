from typing import Any

import agents
from agents.tracing.processor_interface import TracingProcessor
from agents.tracing.spans import Span as OaiSpan
from agents.tracing.traces import Trace as OaiTrace

from ddtrace.internal.logger import get_logger
from ddtrace.trace import Pin


logger = get_logger(__name__)


class LLMObsTraceProcessor(TracingProcessor):
    def __init__(self, integration):
        super().__init__()
        self.integration = integration

    def on_span_start(self, span: OaiSpan[Any]) -> None:
        """Called when a span starts.

        Args:
            span: The span that started.
        """
        self.integration.start_span_from_oai_span(Pin.get_from(agents), raw_oai_span=span)

    def on_trace_start(self, trace: OaiTrace) -> None:
        """Called when a trace starts.

        Args:
            trace: The trace that started.
        """
        self.integration.start_span_from_oai_trace(Pin.get_from(agents), raw_oai_trace=trace)

    def on_trace_end(self, trace: OaiTrace) -> None:
        """Called when a trace is finished.

        Args:
            trace: The trace that started.
        """
        cur_trace = self.integration.get_llmobs_span(trace.trace_id)
        if not cur_trace:
            logger.warning("No LLMObs span found for openai trace %s", trace.trace_id)
            return
        self.integration._llmobs_set_tags(
            cur_trace,
            [],
            {"raw_oai_trace": trace},
        )
        cur_trace.finish()

    def on_span_end(self, span: OaiSpan[Any]) -> None:
        """Called when a span is finished. Should not block or raise exceptions.

        Args:
            span: The span that finished.
        """
        llmobs_span = self.integration.get_llmobs_span(span.span_id)
        if not llmobs_span:
            logger.warning("No LLMObs span found for openai span %s", span.span_id)
            return
        self.integration.llmobs_set_tags(llmobs_span, [], {"raw_oai_span": span})
        llmobs_span.finish()

    def force_flush(self) -> bool:
        """Force flush of data to target. Should never throw exception.

        Returns:
            True if the flush succeeded, False otherwise.
        """
        return True

    def shutdown(self) -> None:
        """Shuts down the processor."""
        self.integration.clear_state()


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
        """Called when the application stops."""
        pass

    def force_flush(self) -> None:
        """Forces an immediate flush of all queued spans/traces."""
        pass
