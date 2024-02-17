import mock

from ddtrace._trace.span import Span
from ddtrace.ext import SpanTypes
from ddtrace.llmobs._llmobs import LLMObsTraceProcessor
from tests.utils import DummyTracer


def test_processor_returns_all_traces():
    """Test that the LLMObsTraceProcessor returns all traces."""
    trace_filter = LLMObsTraceProcessor(llmobs_writer=mock.MagicMock())
    root_llm_span = Span(name="span1", span_type=SpanTypes.LLM)
    root_llm_span._local_root = root_llm_span
    trace1 = [root_llm_span]
    assert trace_filter.process_trace(trace1) == trace1


def test_processor_creates_llmobs_span_event():
    mock_llmobs_writer = mock.MagicMock()
    trace_filter = LLMObsTraceProcessor(llmobs_writer=mock_llmobs_writer)
    root_llm_span = Span(name="root", span_type=SpanTypes.LLM)
    trace = [root_llm_span]
    trace_filter.process_trace(trace)
    assert mock_llmobs_writer.enqueue.call_count == 1
    mock_llmobs_writer.assert_has_calls(
        [
            mock.call.enqueue(
                {
                    "span_id": str(root_llm_span.span_id),
                    "trace_id": "{:x}".format(root_llm_span.trace_id),
                    "parent_id": "",
                    "session_id": "{:x}".format(root_llm_span.trace_id),
                    "name": root_llm_span.name,
                    "tags": mock.ANY,
                    "start_ns": root_llm_span.start_ns,
                    "duration": root_llm_span.duration_ns,
                    "error": 0,
                    "meta": mock.ANY,
                    "metrics": mock.ANY,
                },
            ),
        ]
    )


def test_processor_only_creates_llmobs_span_event():
    """Test that the LLMObsTraceProcessor only creates LLMObs span events for LLM span types."""
    dummy_tracer = DummyTracer()
    mock_llmobs_writer = mock.MagicMock()
    trace_filter = LLMObsTraceProcessor(llmobs_writer=mock_llmobs_writer)
    with dummy_tracer.trace("root_llm_span", span_type=SpanTypes.LLM) as root_span:
        with dummy_tracer.trace("child_span") as child_span:
            with dummy_tracer.trace("llm_span", span_type=SpanTypes.LLM) as grandchild_span:
                pass
    trace = [root_span, child_span, grandchild_span]
    trace_filter.process_trace(trace)
    assert mock_llmobs_writer.enqueue.call_count == 2
    mock_llmobs_writer.assert_has_calls(
        [
            mock.call.enqueue(
                {
                    "span_id": str(root_span.span_id),
                    "trace_id": "{:x}".format(root_span.trace_id),
                    "parent_id": "",
                    "session_id": "{:x}".format(root_span.trace_id),
                    "name": root_span.name,
                    "tags": mock.ANY,
                    "start_ns": root_span.start_ns,
                    "duration": root_span.duration_ns,
                    "error": 0,
                    "meta": mock.ANY,
                    "metrics": mock.ANY,
                },
            ),
            mock.call.enqueue(
                {
                    "span_id": str(grandchild_span.span_id),
                    "trace_id": "{:x}".format(grandchild_span.trace_id),
                    "parent_id": str(root_span.span_id),
                    "session_id": "{:x}".format(grandchild_span.trace_id),
                    "name": grandchild_span.name,
                    "tags": mock.ANY,
                    "start_ns": grandchild_span.start_ns,
                    "duration": grandchild_span.duration_ns,
                    "error": 0,
                    "meta": mock.ANY,
                    "metrics": mock.ANY,
                },
            ),
        ]
    )


def test_set_correct_parent_id():
    """Test that the parent_id is set as the span_id of the nearest LLMObs span in the span's ancestor tree."""
    dummy_tracer = DummyTracer()
    with dummy_tracer.trace("root"):
        with dummy_tracer.trace("llm_span", span_type=SpanTypes.LLM) as llm_span:
            pass
    assert LLMObsTraceProcessor._get_llmobs_parent_id(llm_span) is None
    with dummy_tracer.trace("root_llm_span", span_type=SpanTypes.LLM) as root_span:
        with dummy_tracer.trace("child_span") as child_span:
            with dummy_tracer.trace("llm_span", span_type=SpanTypes.LLM) as grandchild_span:
                pass
    assert LLMObsTraceProcessor._get_llmobs_parent_id(root_span) is None
    assert LLMObsTraceProcessor._get_llmobs_parent_id(child_span) is None
    assert LLMObsTraceProcessor._get_llmobs_parent_id(grandchild_span) == root_span.span_id
