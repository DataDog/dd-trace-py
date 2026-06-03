"""Test-only trace processors for LLMObs integration tests."""

from typing import Optional
from unittest import mock

from ddtrace._trace.span import Span
from ddtrace._trace.tracer import Tracer
from ddtrace.ext import SpanTypes
from ddtrace.internal.settings._config import config
from ddtrace.llmobs import _telemetry as telemetry
from ddtrace.llmobs._constants import LLMOBS_STRUCT
from ddtrace.llmobs._constants import LLMOBS_SUBMITTED_TAG_KEY
from ddtrace.llmobs._constants import LLMObsExportMode
from ddtrace.llmobs._llmobs import LLMObs
from ddtrace.llmobs._processor import LLMObsProcessor
from ddtrace.llmobs._utils import assemble_llmobs_span_event
from ddtrace.llmobs._utils import get_llmobs_span_kind


class TestAlwaysEnqueueLLMObsProcessor(LLMObsProcessor):
    """Test-only ``LLMObsProcessor`` that enqueues every assembled LLM span event to the
    writer regardless of export mode, so tests can assert against the LLMObs writer events
    and the rendered payload that intake would extract from ``meta_struct``.

    Unlike the real processor it never scrubs ``meta_struct`` on the ship path; it still scrubs
    on drop (user-processor omit or malformed span) and drops the APM trace when APM tracing is off.
    """

    def __init__(self, llmobs_span_writer) -> None:
        instance = LLMObs._instance
        super().__init__(
            llmobs_span_writer,
            export_mode=instance._export_mode,
            user_span_processor=instance._user_span_processor,
            evaluator_runner=instance._evaluator_runner,
        )

    def process_trace(self, trace: list[Span]) -> Optional[list[Span]]:
        if not trace:
            return trace
        for span in trace:
            if span.span_type != SpanTypes.LLM:
                continue
            self._submit_for_test(span)
        if self._export_mode == LLMObsExportMode.LLMOBS_DIRECT or not config._tracing_enabled:
            return None
        return trace

    def _submit_for_test(self, span: Span) -> None:
        telemetry.record_span_created(span, self._export_mode, self._llmobs_span_writer._agentless)
        span_kind = get_llmobs_span_kind(span)
        try:
            llmobs_data = self._prepare_llmobs_span_data(span, span_kind)
        except (KeyError, TypeError, ValueError):
            llmobs_data = None
        if llmobs_data is None:
            span._remove_struct_tag(LLMOBS_STRUCT.KEY)
            return
        try:
            event = assemble_llmobs_span_event(span, self._export_mode, llmobs_data)
        except (KeyError, TypeError, ValueError):
            event = None
        if event is None:
            span._remove_struct_tag(LLMOBS_STRUCT.KEY)
            return
        if self._evaluator_runner and span_kind == "llm":
            self._evaluator_runner.enqueue(event, span)
        # Always submits via the writer, so mark the span as directly submitted like the real processor.
        span.set_tag(LLMOBS_SUBMITTED_TAG_KEY, "1")
        self._llmobs_span_writer.enqueue(event)


def install_mock_llmobs_writer(tracer: Tracer, mock_writer=None):
    """Stop the real writer, attach a mock, and rebind the LLMObs flush processor.

    ``LLMObs.enable()`` wires ``LLMObsProcessor`` to the real writer; contrib fixtures must
    call this after swapping in a mock so every LLM span event is enqueued for assertions
    and ``meta_struct`` is left intact for the intake-extraction checks.
    """
    if mock_writer is None:
        mock_writer = mock.MagicMock()
    LLMObs._instance._llmobs_span_writer.stop()
    LLMObs._instance._llmobs_span_writer = mock_writer
    tracer._span_aggregator.llmobs_processor = TestAlwaysEnqueueLLMObsProcessor(mock_writer)
    return mock_writer
