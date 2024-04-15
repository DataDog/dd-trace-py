import mock
import pytest

from ddtrace._trace.span import Span
from ddtrace.ext import SpanTypes
from ddtrace.llmobs._constants import ML_APP
from ddtrace.llmobs._constants import SESSION_ID
from ddtrace.llmobs._constants import SPAN_KIND
from ddtrace.llmobs._trace_processor import LLMObsTraceProcessor
from tests.llmobs._utils import _expected_llmobs_llm_span_event
from tests.utils import DummyTracer
from tests.utils import override_global_config


@pytest.fixture
def mock_logs():
    with mock.patch("ddtrace.llmobs._trace_processor.log") as mock_logs:
        yield mock_logs


def test_processor_returns_all_traces():
    """Test that the LLMObsTraceProcessor returns all traces."""
    trace_filter = LLMObsTraceProcessor(llmobs_writer=mock.MagicMock())
    root_llm_span = Span(name="span1", span_type=SpanTypes.LLM)
    root_llm_span.set_tag_str(SPAN_KIND, "llm")
    trace1 = [root_llm_span]
    assert trace_filter.process_trace(trace1) == trace1


def test_processor_creates_llmobs_span_event():
    mock_llmobs_writer = mock.MagicMock()
    trace_filter = LLMObsTraceProcessor(llmobs_writer=mock_llmobs_writer)
    root_llm_span = Span(name="root", span_type=SpanTypes.LLM)
    root_llm_span.set_tag_str(SPAN_KIND, "llm")
    trace = [root_llm_span]
    trace_filter.process_trace(trace)
    assert mock_llmobs_writer.enqueue.call_count == 1
    mock_llmobs_writer.assert_has_calls([mock.call.enqueue(_expected_llmobs_llm_span_event(root_llm_span, "llm"))])


def test_processor_only_creates_llmobs_span_event():
    """Test that the LLMObsTraceProcessor only creates LLMObs span events for LLM span types."""
    dummy_tracer = DummyTracer()
    mock_llmobs_writer = mock.MagicMock()
    trace_filter = LLMObsTraceProcessor(llmobs_writer=mock_llmobs_writer)
    with dummy_tracer.trace("root_llm_span", span_type=SpanTypes.LLM) as root_span:
        root_span.set_tag_str(SPAN_KIND, "llm")
        with dummy_tracer.trace("child_span") as child_span:
            with dummy_tracer.trace("llm_span", span_type=SpanTypes.LLM) as grandchild_span:
                grandchild_span.set_tag_str(SPAN_KIND, "llm")
    trace = [root_span, child_span, grandchild_span]
    expected_grandchild_llmobs_span = _expected_llmobs_llm_span_event(grandchild_span, "llm")
    expected_grandchild_llmobs_span["parent_id"] = str(root_span.span_id)
    trace_filter.process_trace(trace)
    assert mock_llmobs_writer.enqueue.call_count == 2
    mock_llmobs_writer.assert_has_calls(
        [
            mock.call.enqueue(_expected_llmobs_llm_span_event(root_span, "llm")),
            mock.call.enqueue(expected_grandchild_llmobs_span),
        ]
    )


def test_set_correct_parent_id():
    """Test that the parent_id is set as the span_id of the nearest LLMObs span in the span's ancestor tree."""
    dummy_tracer = DummyTracer()
    with dummy_tracer.trace("root"):
        with dummy_tracer.trace("llm_span", span_type=SpanTypes.LLM) as llm_span:
            pass
    tp = LLMObsTraceProcessor(dummy_tracer._writer)
    assert tp._get_llmobs_parent_id(llm_span) is None
    with dummy_tracer.trace("root_llm_span", span_type=SpanTypes.LLM) as root_span:
        with dummy_tracer.trace("child_span") as child_span:
            with dummy_tracer.trace("llm_span", span_type=SpanTypes.LLM) as grandchild_span:
                pass
    assert tp._get_llmobs_parent_id(root_span) is None
    assert tp._get_llmobs_parent_id(child_span) is None
    assert tp._get_llmobs_parent_id(grandchild_span) == root_span.span_id


def test_propagate_session_id_from_ancestors():
    """
    Test that session_id is propagated from the nearest LLMObs span in the span's ancestor tree
    if no session_id is not set on the span itself.
    """
    dummy_tracer = DummyTracer()
    with dummy_tracer.trace("root_llm_span", span_type=SpanTypes.LLM) as root_span:
        root_span.set_tag_str(SESSION_ID, "test_session_id")
        with dummy_tracer.trace("child_span"):
            with dummy_tracer.trace("llm_span", span_type=SpanTypes.LLM) as llm_span:
                pass
    tp = LLMObsTraceProcessor(dummy_tracer._writer)
    assert tp._get_session_id(llm_span) == "test_session_id"


def test_session_id_if_set_manually():
    """Test that session_id is extracted from the span if it is already set manually."""
    dummy_tracer = DummyTracer()
    with dummy_tracer.trace("root_llm_span", span_type=SpanTypes.LLM) as root_span:
        root_span.set_tag_str(SESSION_ID, "test_session_id")
        with dummy_tracer.trace("child_span"):
            with dummy_tracer.trace("llm_span", span_type=SpanTypes.LLM) as llm_span:
                llm_span.set_tag_str(SESSION_ID, "test_different_session_id")
    tp = LLMObsTraceProcessor(dummy_tracer._writer)
    assert tp._get_session_id(llm_span) == "test_different_session_id"


def test_session_id_defaults_to_trace_id():
    """Test that session_id defaults to the span's trace ID if not set nor found upstream."""
    dummy_tracer = DummyTracer()
    with dummy_tracer.trace("root_llm_span", span_type=SpanTypes.LLM):
        with dummy_tracer.trace("child_span"):
            with dummy_tracer.trace("llm_span", span_type=SpanTypes.LLM) as llm_span:
                pass
    tp = LLMObsTraceProcessor(dummy_tracer._writer)
    assert tp._get_session_id(llm_span) == "{:x}".format(llm_span.trace_id)


def test_ml_app_tag_defaults_to_env_var():
    """Test that no ml_app defaults to the environment variable DD_LLMOBS_APP_NAME."""
    with override_global_config(dict(_llmobs_ml_app="<not-a-real-app-name>")):
        dummy_tracer = DummyTracer()
        with dummy_tracer.trace("root_llm_span", span_type=SpanTypes.LLM) as llm_span:
            pass
        tp = LLMObsTraceProcessor(dummy_tracer._writer)
        assert "ml_app:<not-a-real-app-name>" in tp._llmobs_tags(llm_span)


def test_ml_app_tag_overrides_env_var():
    """Test that when ml_app is set on the span, it overrides the environment variable DD_LLMOBS_APP_NAME."""
    with override_global_config(dict(_llmobs_ml_app="<not-a-real-app-name>")):
        dummy_tracer = DummyTracer()
        with dummy_tracer.trace("root_llm_span", span_type=SpanTypes.LLM) as llm_span:
            llm_span.set_tag(ML_APP, "test-ml-app")
        tp = LLMObsTraceProcessor(dummy_tracer._writer)
        assert "ml_app:test-ml-app" in tp._llmobs_tags(llm_span)


def test_malformed_span_logs_error_instead_of_raising(mock_logs):
    """
    Test that a trying to create a span event from a malformed span will log an error instead of crashing.
    """
    dummy_tracer = DummyTracer()
    mock_llmobs_writer = mock.MagicMock()
    with dummy_tracer.trace("root_llm_span", span_type=SpanTypes.LLM) as llm_span:
        # span does not have SPAN_KIND tag
        pass
    tp = LLMObsTraceProcessor(llmobs_writer=mock_llmobs_writer)
    tp.process_trace([llm_span])
    mock_logs.error.assert_called_once_with(
        "Error generating LLMObs span event for span %s, likely due to malformed span", llm_span
    )
    mock_llmobs_writer.enqueue.assert_not_called()
