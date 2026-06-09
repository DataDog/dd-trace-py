"""Test-only trace processors for LLMObs integration tests."""

from typing import Optional
from unittest import mock

from ddtrace._trace.processor import TraceProcessor
from ddtrace._trace.span import Span
from ddtrace._trace.tracer import Tracer
from ddtrace.ext import SpanTypes
from ddtrace.internal.settings import env as _env
from ddtrace.internal.utils.formats import asbool
from ddtrace.llmobs._constants import CACHED_LLMOBS_EVENT_CTX_KEY
from ddtrace.llmobs._constants import LLMOBS_SUBMITTED_TAG_KEY
from ddtrace.llmobs._llmobs import LLMObs


class TestAlwaysEnqueueLLMObsProcessor(TraceProcessor):
    """Test-only variant of ``LLMObsProcessor``: always enqueue and
    never scrub meta_struct. Lets tests assert against both the LLMObs writer events
    and the rendered payload that intake would extract from meta_struct.

    Mirrors ``LLMObsProcessor``'s APM trace-drop behaviour so that tests covering
    ``DD_APM_TRACING_ENABLED=false`` still see the trace dropped.
    """

    def __init__(self, llmobs_span_writer) -> None:
        super().__init__()
        self._llmobs_span_writer = llmobs_span_writer

    def process_trace(self, trace: list[Span]) -> Optional[list[Span]]:
        if not trace:
            return trace
        drop_apm_trace = not asbool(_env.get("DD_APM_TRACING_ENABLED", "true"))
        for span in trace:
            if span.span_type != SpanTypes.LLM:
                continue
            if span.get_tag(LLMOBS_SUBMITTED_TAG_KEY) == "1":
                continue
            event = span._get_ctx_item(CACHED_LLMOBS_EVENT_CTX_KEY)
            if event is None:
                continue
            self._llmobs_span_writer.enqueue(event)
            span.set_tag(LLMOBS_SUBMITTED_TAG_KEY, "1")
        return None if drop_apm_trace else trace


def install_mock_llmobs_writer(tracer: Tracer, mock_writer=None):
    """Stop the real writer, attach a mock, and rebind the fallback processor.

    ``LLMObs.enable()`` wires ``LLMObsProcessor`` to the real writer;
    contrib fixtures must call this after swapping in a mock so meta_struct is not
    scrubbed when the SDK predicts the trace will be dropped.
    """
    if mock_writer is None:
        mock_writer = mock.MagicMock()
    LLMObs._instance._llmobs_span_writer.stop()
    LLMObs._instance._llmobs_span_writer = mock_writer
    tracer._span_aggregator.llmobs_processor = TestAlwaysEnqueueLLMObsProcessor(mock_writer)
    return mock_writer
