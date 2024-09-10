import mock
import pytest

from ddtrace._trace.span import Span
from ddtrace.ext import SpanTypes
from ddtrace.llmobs._constants import INPUT_MESSAGES
from ddtrace.llmobs._constants import INPUT_PARAMETERS
from ddtrace.llmobs._constants import INPUT_VALUE
from ddtrace.llmobs._constants import LANGCHAIN_APM_SPAN_NAME
from ddtrace.llmobs._constants import METADATA
from ddtrace.llmobs._constants import METRICS
from ddtrace.llmobs._constants import ML_APP
from ddtrace.llmobs._constants import MODEL_NAME
from ddtrace.llmobs._constants import MODEL_PROVIDER
from ddtrace.llmobs._constants import OUTPUT_MESSAGES
from ddtrace.llmobs._constants import OUTPUT_VALUE
from ddtrace.llmobs._constants import SESSION_ID
from ddtrace.llmobs._constants import SPAN_KIND
from ddtrace.llmobs._trace_processor import LLMObsTraceProcessor
from ddtrace.llmobs._utils import _get_llmobs_parent_id
from ddtrace.llmobs._utils import _get_session_id
from tests.llmobs._utils import _expected_llmobs_llm_span_event
from tests.utils import DummyTracer
from tests.utils import override_global_config


@pytest.fixture
def mock_logs():
    with mock.patch("ddtrace.llmobs._trace_processor.log") as mock_logs:
        yield mock_logs


def test_processor_returns_all_traces_by_default():
    """Test that the LLMObsTraceProcessor returns all traces by default."""
    trace_filter = LLMObsTraceProcessor(llmobs_span_writer=mock.MagicMock())
    root_llm_span = Span(name="span1", span_type=SpanTypes.LLM)
    root_llm_span.set_tag_str(SPAN_KIND, "llm")
    trace1 = [root_llm_span]
    assert trace_filter.process_trace(trace1) == trace1


def test_processor_returns_all_traces_if_not_agentless():
    """Test that the LLMObsTraceProcessor returns all traces if DD_LLMOBS_AGENTLESS_ENABLED is not set to true."""
    with override_global_config(dict(_llmobs_agentless_enabled=False)):
        trace_filter = LLMObsTraceProcessor(llmobs_span_writer=mock.MagicMock())
        root_llm_span = Span(name="span1", span_type=SpanTypes.LLM)
        root_llm_span.set_tag_str(SPAN_KIND, "llm")
        trace1 = [root_llm_span]
        assert trace_filter.process_trace(trace1) == trace1


def test_processor_returns_none_in_agentless_mode():
    """Test that the LLMObsTraceProcessor returns None if DD_LLMOBS_AGENTLESS_ENABLED is set to true."""
    with override_global_config(dict(_llmobs_agentless_enabled=True)):
        trace_filter = LLMObsTraceProcessor(llmobs_span_writer=mock.MagicMock())
        root_llm_span = Span(name="span1", span_type=SpanTypes.LLM)
        root_llm_span.set_tag_str(SPAN_KIND, "llm")
        trace1 = [root_llm_span]
        assert trace_filter.process_trace(trace1) is None


def test_processor_creates_llmobs_span_event():
    with override_global_config(dict(_llmobs_ml_app="unnamed-ml-app")):
        mock_llmobs_span_writer = mock.MagicMock()
        trace_filter = LLMObsTraceProcessor(llmobs_span_writer=mock_llmobs_span_writer)
        root_llm_span = Span(name="root", span_type=SpanTypes.LLM)
        root_llm_span.set_tag_str(SPAN_KIND, "llm")
        trace = [root_llm_span]
        trace_filter.process_trace(trace)
    assert mock_llmobs_span_writer.enqueue.call_count == 1
    mock_llmobs_span_writer.assert_has_calls([mock.call.enqueue(_expected_llmobs_llm_span_event(root_llm_span, "llm"))])


def test_processor_only_creates_llmobs_span_event():
    """Test that the LLMObsTraceProcessor only creates LLMObs span events for LLM span types."""
    dummy_tracer = DummyTracer()
    mock_llmobs_span_writer = mock.MagicMock()
    trace_filter = LLMObsTraceProcessor(llmobs_span_writer=mock_llmobs_span_writer)
    with override_global_config(dict(_llmobs_ml_app="unnamed-ml-app")):
        with dummy_tracer.trace("root_llm_span", span_type=SpanTypes.LLM) as root_span:
            root_span.set_tag_str(SPAN_KIND, "llm")
            with dummy_tracer.trace("child_span") as child_span:
                with dummy_tracer.trace("llm_span", span_type=SpanTypes.LLM) as grandchild_span:
                    grandchild_span.set_tag_str(SPAN_KIND, "llm")
        trace = [root_span, child_span, grandchild_span]
        expected_grandchild_llmobs_span = _expected_llmobs_llm_span_event(grandchild_span, "llm")
        expected_grandchild_llmobs_span["parent_id"] = str(root_span.span_id)
        trace_filter.process_trace(trace)
    assert mock_llmobs_span_writer.enqueue.call_count == 2
    mock_llmobs_span_writer.assert_has_calls(
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
    assert _get_llmobs_parent_id(llm_span) is None
    with dummy_tracer.trace("root_llm_span", span_type=SpanTypes.LLM) as root_span:
        with dummy_tracer.trace("child_span") as child_span:
            with dummy_tracer.trace("llm_span", span_type=SpanTypes.LLM) as grandchild_span:
                pass
    assert _get_llmobs_parent_id(root_span) is None
    assert _get_llmobs_parent_id(child_span) == str(root_span.span_id)
    assert _get_llmobs_parent_id(grandchild_span) == str(root_span.span_id)


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
    assert _get_session_id(llm_span) == "test_session_id"


def test_session_id_if_set_manually():
    """Test that session_id is extracted from the span if it is already set manually."""
    dummy_tracer = DummyTracer()
    with dummy_tracer.trace("root_llm_span", span_type=SpanTypes.LLM) as root_span:
        root_span.set_tag_str(SESSION_ID, "test_session_id")
        with dummy_tracer.trace("child_span"):
            with dummy_tracer.trace("llm_span", span_type=SpanTypes.LLM) as llm_span:
                llm_span.set_tag_str(SESSION_ID, "test_different_session_id")
    assert _get_session_id(llm_span) == "test_different_session_id"


def test_session_id_propagates_ignore_non_llmobs_spans():
    """
    Test that when session_id is not set, we propagate from nearest LLMObs ancestor
    even if there are non-LLMObs spans in between.
    """
    dummy_tracer = DummyTracer()
    with override_global_config(dict(_llmobs_ml_app="<not-a-real-app-name>")):
        with dummy_tracer.trace("root_llm_span", span_type=SpanTypes.LLM) as llm_span:
            llm_span.set_tag_str(SPAN_KIND, "llm")
            llm_span.set_tag_str(SESSION_ID, "session-123")
            with dummy_tracer.trace("child_span"):
                with dummy_tracer.trace("llm_grandchild_span", span_type=SpanTypes.LLM) as grandchild_span:
                    grandchild_span.set_tag_str(SPAN_KIND, "llm")
                    with dummy_tracer.trace("great_grandchild_span", span_type=SpanTypes.LLM) as great_grandchild_span:
                        great_grandchild_span.set_tag_str(SPAN_KIND, "llm")
        tp = LLMObsTraceProcessor(dummy_tracer._writer)
        llm_span_event = tp._llmobs_span_event(llm_span)
        grandchild_span_event = tp._llmobs_span_event(grandchild_span)
        great_grandchild_span_event = tp._llmobs_span_event(great_grandchild_span)
    assert llm_span_event["session_id"] == "session-123"
    assert grandchild_span_event["session_id"] == "session-123"
    assert great_grandchild_span_event["session_id"] == "session-123"


def test_ml_app_tag_defaults_to_env_var():
    """Test that no ml_app defaults to the environment variable DD_LLMOBS_ML_APP."""
    dummy_tracer = DummyTracer()
    with override_global_config(dict(_llmobs_ml_app="<not-a-real-app-name>")):
        with dummy_tracer.trace("root_llm_span", span_type=SpanTypes.LLM) as llm_span:
            llm_span.set_tag_str(SPAN_KIND, "llm")
            pass
        tp = LLMObsTraceProcessor(dummy_tracer._writer)
        span_event = tp._llmobs_span_event(llm_span)
        assert "ml_app:<not-a-real-app-name>" in span_event["tags"]


def test_ml_app_tag_overrides_env_var():
    """Test that when ml_app is set on the span, it overrides the environment variable DD_LLMOBS_ML_APP."""
    dummy_tracer = DummyTracer()
    with override_global_config(dict(_llmobs_ml_app="<not-a-real-app-name>")):
        with dummy_tracer.trace("root_llm_span", span_type=SpanTypes.LLM) as llm_span:
            llm_span.set_tag_str(SPAN_KIND, "llm")
            llm_span.set_tag(ML_APP, "test-ml-app")
        tp = LLMObsTraceProcessor(dummy_tracer._writer)
        span_event = tp._llmobs_span_event(llm_span)
        assert "ml_app:test-ml-app" in span_event["tags"]


def test_ml_app_propagates_ignore_non_llmobs_spans():
    """
    Test that when ml_app is not set, we propagate from nearest LLMObs ancestor
    even if there are non-LLMObs spans in between.
    """
    dummy_tracer = DummyTracer()
    with override_global_config(dict(_llmobs_ml_app="<not-a-real-app-name>")):
        with dummy_tracer.trace("root_llm_span", span_type=SpanTypes.LLM) as llm_span:
            llm_span.set_tag_str(SPAN_KIND, "llm")
            llm_span.set_tag(ML_APP, "test-ml-app")
            with dummy_tracer.trace("child_span"):
                with dummy_tracer.trace("llm_grandchild_span", span_type=SpanTypes.LLM) as grandchild_span:
                    grandchild_span.set_tag_str(SPAN_KIND, "llm")
                    with dummy_tracer.trace("great_grandchild_span", span_type=SpanTypes.LLM) as great_grandchild_span:
                        great_grandchild_span.set_tag_str(SPAN_KIND, "llm")
        tp = LLMObsTraceProcessor(dummy_tracer._writer)
        llm_span_event = tp._llmobs_span_event(llm_span)
        grandchild_span_event = tp._llmobs_span_event(grandchild_span)
        great_grandchild_span_event = tp._llmobs_span_event(great_grandchild_span)
        assert "ml_app:test-ml-app" in llm_span_event["tags"]
        assert "ml_app:test-ml-app" in grandchild_span_event["tags"]
        assert "ml_app:test-ml-app" in great_grandchild_span_event["tags"]


def test_malformed_span_logs_error_instead_of_raising(mock_logs):
    """Test that a trying to create a span event from a malformed span will log an error instead of crashing."""
    dummy_tracer = DummyTracer()
    mock_llmobs_span_writer = mock.MagicMock()
    with dummy_tracer.trace("root_llm_span", span_type=SpanTypes.LLM) as llm_span:
        # span does not have SPAN_KIND tag
        pass
    tp = LLMObsTraceProcessor(llmobs_span_writer=mock_llmobs_span_writer)
    tp.process_trace([llm_span])
    mock_logs.error.assert_called_once_with(
        "Error generating LLMObs span event for span %s, likely due to malformed span", llm_span
    )
    mock_llmobs_span_writer.enqueue.assert_not_called()


def test_model_and_provider_are_set():
    """Test that model and provider are set on the span event if they are present on the LLM-kind span."""
    dummy_tracer = DummyTracer()
    mock_llmobs_span_writer = mock.MagicMock()
    with override_global_config(dict(_llmobs_ml_app="unnamed-ml-app")):
        with dummy_tracer.trace("root_llm_span", span_type=SpanTypes.LLM) as llm_span:
            llm_span.set_tag(SPAN_KIND, "llm")
            llm_span.set_tag(MODEL_NAME, "model_name")
            llm_span.set_tag(MODEL_PROVIDER, "model_provider")
        tp = LLMObsTraceProcessor(llmobs_span_writer=mock_llmobs_span_writer)
        span_event = tp._llmobs_span_event(llm_span)
    assert span_event["meta"]["model_name"] == "model_name"
    assert span_event["meta"]["model_provider"] == "model_provider"


def test_model_provider_defaults_to_custom():
    """Test that model provider defaults to "custom" if not provided."""
    dummy_tracer = DummyTracer()
    mock_llmobs_span_writer = mock.MagicMock()
    with override_global_config(dict(_llmobs_ml_app="unnamed-ml-app")):
        with dummy_tracer.trace("root_llm_span", span_type=SpanTypes.LLM) as llm_span:
            llm_span.set_tag(SPAN_KIND, "llm")
            llm_span.set_tag(MODEL_NAME, "model_name")
        tp = LLMObsTraceProcessor(llmobs_span_writer=mock_llmobs_span_writer)
        span_event = tp._llmobs_span_event(llm_span)
    assert span_event["meta"]["model_name"] == "model_name"
    assert span_event["meta"]["model_provider"] == "custom"


def test_model_not_set_if_not_llm_kind_span():
    """Test that model name and provider not set if non-LLM span."""
    dummy_tracer = DummyTracer()
    mock_llmobs_span_writer = mock.MagicMock()
    with override_global_config(dict(_llmobs_ml_app="unnamed-ml-app")):
        with dummy_tracer.trace("root_workflow_span", span_type=SpanTypes.LLM) as span:
            span.set_tag(SPAN_KIND, "workflow")
            span.set_tag(MODEL_NAME, "model_name")
        tp = LLMObsTraceProcessor(llmobs_span_writer=mock_llmobs_span_writer)
        span_event = tp._llmobs_span_event(span)
    assert "model_name" not in span_event["meta"]
    assert "model_provider" not in span_event["meta"]


def test_input_messages_are_set():
    """Test that input messages are set on the span event if they are present on the span."""
    dummy_tracer = DummyTracer()
    mock_llmobs_span_writer = mock.MagicMock()
    with override_global_config(dict(_llmobs_ml_app="unnamed-ml-app")):
        with dummy_tracer.trace("root_llm_span", span_type=SpanTypes.LLM) as llm_span:
            llm_span.set_tag(SPAN_KIND, "llm")
            llm_span.set_tag(INPUT_MESSAGES, '[{"content": "message", "role": "user"}]')
        tp = LLMObsTraceProcessor(llmobs_span_writer=mock_llmobs_span_writer)
        assert tp._llmobs_span_event(llm_span)["meta"]["input"]["messages"] == [{"content": "message", "role": "user"}]


def test_input_value_is_set():
    """Test that input value is set on the span event if they are present on the span."""
    dummy_tracer = DummyTracer()
    mock_llmobs_span_writer = mock.MagicMock()
    with override_global_config(dict(_llmobs_ml_app="unnamed-ml-app")):
        with dummy_tracer.trace("root_llm_span", span_type=SpanTypes.LLM) as llm_span:
            llm_span.set_tag(SPAN_KIND, "llm")
            llm_span.set_tag(INPUT_VALUE, "value")
        tp = LLMObsTraceProcessor(llmobs_span_writer=mock_llmobs_span_writer)
        assert tp._llmobs_span_event(llm_span)["meta"]["input"]["value"] == "value"


def test_input_parameters_are_set():
    """Test that input parameters are set on the span event if they are present on the span."""
    dummy_tracer = DummyTracer()
    mock_llmobs_span_writer = mock.MagicMock()
    with override_global_config(dict(_llmobs_ml_app="unnamed-ml-app")):
        with dummy_tracer.trace("root_llm_span", span_type=SpanTypes.LLM) as llm_span:
            llm_span.set_tag(SPAN_KIND, "llm")
            llm_span.set_tag(INPUT_PARAMETERS, '{"key": "value"}')
        tp = LLMObsTraceProcessor(llmobs_span_writer=mock_llmobs_span_writer)
        assert tp._llmobs_span_event(llm_span)["meta"]["input"]["parameters"] == {"key": "value"}


def test_output_messages_are_set():
    """Test that output messages are set on the span event if they are present on the span."""
    dummy_tracer = DummyTracer()
    mock_llmobs_span_writer = mock.MagicMock()
    with override_global_config(dict(_llmobs_ml_app="unnamed-ml-app")):
        with dummy_tracer.trace("root_llm_span", span_type=SpanTypes.LLM) as llm_span:
            llm_span.set_tag(SPAN_KIND, "llm")
            llm_span.set_tag(OUTPUT_MESSAGES, '[{"content": "message", "role": "user"}]')
        tp = LLMObsTraceProcessor(llmobs_span_writer=mock_llmobs_span_writer)
        assert tp._llmobs_span_event(llm_span)["meta"]["output"]["messages"] == [{"content": "message", "role": "user"}]


def test_output_value_is_set():
    """Test that output value is set on the span event if they are present on the span."""
    dummy_tracer = DummyTracer()
    mock_llmobs_span_writer = mock.MagicMock()
    with override_global_config(dict(_llmobs_ml_app="unnamed-ml-app")):
        with dummy_tracer.trace("root_llm_span", span_type=SpanTypes.LLM) as llm_span:
            llm_span.set_tag(SPAN_KIND, "llm")
            llm_span.set_tag(OUTPUT_VALUE, "value")
        tp = LLMObsTraceProcessor(llmobs_span_writer=mock_llmobs_span_writer)
        assert tp._llmobs_span_event(llm_span)["meta"]["output"]["value"] == "value"


def test_metadata_is_set():
    """Test that metadata is set on the span event if it is present on the span."""
    dummy_tracer = DummyTracer()
    mock_llmobs_span_writer = mock.MagicMock()
    with override_global_config(dict(_llmobs_ml_app="unnamed-ml-app")):
        with dummy_tracer.trace("root_llm_span", span_type=SpanTypes.LLM) as llm_span:
            llm_span.set_tag(SPAN_KIND, "llm")
            llm_span.set_tag(METADATA, '{"key": "value"}')
        tp = LLMObsTraceProcessor(llmobs_span_writer=mock_llmobs_span_writer)
        assert tp._llmobs_span_event(llm_span)["meta"]["metadata"] == {"key": "value"}


def test_metrics_are_set():
    """Test that metadata is set on the span event if it is present on the span."""
    dummy_tracer = DummyTracer()
    mock_llmobs_span_writer = mock.MagicMock()
    with override_global_config(dict(_llmobs_ml_app="unnamed-ml-app")):
        with dummy_tracer.trace("root_llm_span", span_type=SpanTypes.LLM) as llm_span:
            llm_span.set_tag(SPAN_KIND, "llm")
            llm_span.set_tag(METRICS, '{"tokens": 100}')
        tp = LLMObsTraceProcessor(llmobs_span_writer=mock_llmobs_span_writer)
        assert tp._llmobs_span_event(llm_span)["metrics"] == {"tokens": 100}


def test_langchain_span_name_is_set_to_class_name():
    """Test span names for langchain auto-instrumented spans is set correctly."""
    dummy_tracer = DummyTracer()
    mock_llmobs_span_writer = mock.MagicMock()
    with override_global_config(dict(_llmobs_ml_app="unnamed-ml-app")):
        with dummy_tracer.trace(LANGCHAIN_APM_SPAN_NAME, resource="expected_name", span_type=SpanTypes.LLM) as llm_span:
            llm_span.set_tag(SPAN_KIND, "llm")
        tp = LLMObsTraceProcessor(llmobs_span_writer=mock_llmobs_span_writer)
        assert tp._llmobs_span_event(llm_span)["name"] == "expected_name"


def test_error_is_set():
    """Test that error is set on the span event if it is present on the span."""
    dummy_tracer = DummyTracer()
    mock_llmobs_span_writer = mock.MagicMock()
    with override_global_config(dict(_llmobs_ml_app="unnamed-ml-app")):
        with pytest.raises(ValueError):
            with dummy_tracer.trace("root_llm_span", span_type=SpanTypes.LLM) as llm_span:
                llm_span.set_tag(SPAN_KIND, "llm")
                raise ValueError("error")
        tp = LLMObsTraceProcessor(llmobs_span_writer=mock_llmobs_span_writer)
        span_event = tp._llmobs_span_event(llm_span)
    assert span_event["meta"]["error.message"] == "error"
    assert "ValueError" in span_event["meta"]["error.type"]
    assert 'raise ValueError("error")' in span_event["meta"]["error.stack"]
