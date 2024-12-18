import mock
import pytest

from ddtrace._trace.span import Span
from ddtrace.ext import SpanTypes
from ddtrace.llmobs._constants import INPUT_MESSAGES
from ddtrace.llmobs._constants import INPUT_PARAMETERS
from ddtrace.llmobs._constants import INPUT_PROMPT
from ddtrace.llmobs._constants import INPUT_VALUE
from ddtrace.llmobs._constants import LANGCHAIN_APM_SPAN_NAME
from ddtrace.llmobs._constants import METADATA
from ddtrace.llmobs._constants import METRICS
from ddtrace.llmobs._constants import MODEL_NAME
from ddtrace.llmobs._constants import MODEL_PROVIDER
from ddtrace.llmobs._constants import OUTPUT_MESSAGES
from ddtrace.llmobs._constants import OUTPUT_VALUE
from ddtrace.llmobs._constants import SPAN_KIND
from ddtrace.llmobs._trace_processor import LLMObsTraceProcessor
from tests.llmobs._utils import _expected_llmobs_llm_span_event


def test_processor_creates_llmobs_span_event():
    with override_global_config(dict(_llmobs_ml_app="unnamed-ml-app")):
        mock_llmobs_span_writer = mock.MagicMock()
        trace_filter = LLMObsTraceProcessor(llmobs_span_writer=mock_llmobs_span_writer)
        root_llm_span = Span(name="root", span_type=SpanTypes.LLM)
        root_llm_span._set_ctx_item(SPAN_KIND, "llm")
        trace = [root_llm_span]
        trace_filter.process_trace(trace)
    assert mock_llmobs_span_writer.enqueue.call_count == 1
    mock_llmobs_span_writer.assert_has_calls(
        [mock.call.enqueue(_expected_llmobs_llm_span_event(root_llm_span, "llm", tags={"service": ""}))]
    )


def test_model_and_provider_are_set():
    """Test that model and provider are set on the span event if they are present on the LLM-kind span."""
    dummy_tracer = DummyTracer()
    mock_llmobs_span_writer = mock.MagicMock()
    with override_global_config(dict(_llmobs_ml_app="unnamed-ml-app")):
        with dummy_tracer.trace("root_llm_span", span_type=SpanTypes.LLM) as llm_span:
            llm_span._set_ctx_item(SPAN_KIND, "llm")
            llm_span._set_ctx_item(MODEL_NAME, "model_name")
            llm_span._set_ctx_item(MODEL_PROVIDER, "model_provider")
        tp = LLMObsTraceProcessor(llmobs_span_writer=mock_llmobs_span_writer)
        span_event, _ = tp._llmobs_span_event(llm_span)
    assert span_event["meta"]["model_name"] == "model_name"
    assert span_event["meta"]["model_provider"] == "model_provider"


def test_model_provider_defaults_to_custom():
    """Test that model provider defaults to "custom" if not provided."""
    dummy_tracer = DummyTracer()
    mock_llmobs_span_writer = mock.MagicMock()
    with override_global_config(dict(_llmobs_ml_app="unnamed-ml-app")):
        with dummy_tracer.trace("root_llm_span", span_type=SpanTypes.LLM) as llm_span:
            llm_span._set_ctx_item(SPAN_KIND, "llm")
            llm_span._set_ctx_item(MODEL_NAME, "model_name")
        tp = LLMObsTraceProcessor(llmobs_span_writer=mock_llmobs_span_writer)
        span_event, _ = tp._llmobs_span_event(llm_span)
    assert span_event["meta"]["model_name"] == "model_name"
    assert span_event["meta"]["model_provider"] == "custom"


def test_model_not_set_if_not_llm_kind_span():
    """Test that model name and provider not set if non-LLM span."""
    dummy_tracer = DummyTracer()
    mock_llmobs_span_writer = mock.MagicMock()
    with override_global_config(dict(_llmobs_ml_app="unnamed-ml-app")):
        with dummy_tracer.trace("root_workflow_span", span_type=SpanTypes.LLM) as span:
            span._set_ctx_item(SPAN_KIND, "workflow")
            span._set_ctx_item(MODEL_NAME, "model_name")
        tp = LLMObsTraceProcessor(llmobs_span_writer=mock_llmobs_span_writer)
        span_event, _ = tp._llmobs_span_event(span)
    assert "model_name" not in span_event["meta"]
    assert "model_provider" not in span_event["meta"]


def test_input_messages_are_set():
    """Test that input messages are set on the span event if they are present on the span."""
    dummy_tracer = DummyTracer()
    mock_llmobs_span_writer = mock.MagicMock()
    with override_global_config(dict(_llmobs_ml_app="unnamed-ml-app")):
        with dummy_tracer.trace("root_llm_span", span_type=SpanTypes.LLM) as llm_span:
            llm_span._set_ctx_item(SPAN_KIND, "llm")
            llm_span._set_ctx_item(INPUT_MESSAGES, [{"content": "message", "role": "user"}])
        tp = LLMObsTraceProcessor(llmobs_span_writer=mock_llmobs_span_writer)
        assert tp._llmobs_span_event(llm_span)[0]["meta"]["input"]["messages"] == [
            {"content": "message", "role": "user"}
        ]


def test_input_value_is_set():
    """Test that input value is set on the span event if they are present on the span."""
    dummy_tracer = DummyTracer()
    mock_llmobs_span_writer = mock.MagicMock()
    with override_global_config(dict(_llmobs_ml_app="unnamed-ml-app")):
        with dummy_tracer.trace("root_llm_span", span_type=SpanTypes.LLM) as llm_span:
            llm_span._set_ctx_item(SPAN_KIND, "llm")
            llm_span._set_ctx_item(INPUT_VALUE, "value")
        tp = LLMObsTraceProcessor(llmobs_span_writer=mock_llmobs_span_writer)
        assert tp._llmobs_span_event(llm_span)[0]["meta"]["input"]["value"] == "value"


def test_input_parameters_are_set():
    """Test that input parameters are set on the span event if they are present on the span."""
    dummy_tracer = DummyTracer()
    mock_llmobs_span_writer = mock.MagicMock()
    with override_global_config(dict(_llmobs_ml_app="unnamed-ml-app")):
        with dummy_tracer.trace("root_llm_span", span_type=SpanTypes.LLM) as llm_span:
            llm_span._set_ctx_item(SPAN_KIND, "llm")
            llm_span._set_ctx_item(INPUT_PARAMETERS, {"key": "value"})
        tp = LLMObsTraceProcessor(llmobs_span_writer=mock_llmobs_span_writer)
        assert tp._llmobs_span_event(llm_span)[0]["meta"]["input"]["parameters"] == {"key": "value"}


def test_output_messages_are_set():
    """Test that output messages are set on the span event if they are present on the span."""
    dummy_tracer = DummyTracer()
    mock_llmobs_span_writer = mock.MagicMock()
    with override_global_config(dict(_llmobs_ml_app="unnamed-ml-app")):
        with dummy_tracer.trace("root_llm_span", span_type=SpanTypes.LLM) as llm_span:
            llm_span._set_ctx_item(SPAN_KIND, "llm")
            llm_span._set_ctx_item(OUTPUT_MESSAGES, [{"content": "message", "role": "user"}])
        tp = LLMObsTraceProcessor(llmobs_span_writer=mock_llmobs_span_writer)
        assert tp._llmobs_span_event(llm_span)[0]["meta"]["output"]["messages"] == [
            {"content": "message", "role": "user"}
        ]


def test_output_value_is_set():
    """Test that output value is set on the span event if they are present on the span."""
    dummy_tracer = DummyTracer()
    mock_llmobs_span_writer = mock.MagicMock()
    with override_global_config(dict(_llmobs_ml_app="unnamed-ml-app")):
        with dummy_tracer.trace("root_llm_span", span_type=SpanTypes.LLM) as llm_span:
            llm_span._set_ctx_item(SPAN_KIND, "llm")
            llm_span._set_ctx_item(OUTPUT_VALUE, "value")
        tp = LLMObsTraceProcessor(llmobs_span_writer=mock_llmobs_span_writer)
        assert tp._llmobs_span_event(llm_span)[0]["meta"]["output"]["value"] == "value"


def test_prompt_is_set():
    """Test that prompt is set on the span event if they are present on the span."""
    dummy_tracer = DummyTracer()
    mock_llmobs_span_writer = mock.MagicMock()
    with override_global_config(dict(_llmobs_ml_app="unnamed-ml-app")):
        with dummy_tracer.trace("root_llm_span", span_type=SpanTypes.LLM) as llm_span:
            llm_span._set_ctx_item(SPAN_KIND, "llm")
            llm_span._set_ctx_item(INPUT_PROMPT, {"variables": {"var1": "var2"}})
        tp = LLMObsTraceProcessor(llmobs_span_writer=mock_llmobs_span_writer)
        assert tp._llmobs_span_event(llm_span)[0]["meta"]["input"]["prompt"] == {"variables": {"var1": "var2"}}


def test_prompt_is_not_set_for_non_llm_spans():
    """Test that prompt is NOT set on the span event if the span is not an LLM span."""
    dummy_tracer = DummyTracer()
    mock_llmobs_span_writer = mock.MagicMock()
    with override_global_config(dict(_llmobs_ml_app="unnamed-ml-app")):
        with dummy_tracer.trace("task_span", span_type=SpanTypes.LLM) as task_span:
            task_span._set_ctx_item(SPAN_KIND, "task")
            task_span._set_ctx_item(INPUT_VALUE, "ival")
            task_span._set_ctx_item(INPUT_PROMPT, {"variables": {"var1": "var2"}})
        tp = LLMObsTraceProcessor(llmobs_span_writer=mock_llmobs_span_writer)
        assert tp._llmobs_span_event(task_span)[0]["meta"]["input"].get("prompt") is None


def test_metadata_is_set():
    """Test that metadata is set on the span event if it is present on the span."""
    dummy_tracer = DummyTracer()
    mock_llmobs_span_writer = mock.MagicMock()
    with override_global_config(dict(_llmobs_ml_app="unnamed-ml-app")):
        with dummy_tracer.trace("root_llm_span", span_type=SpanTypes.LLM) as llm_span:
            llm_span._set_ctx_item(SPAN_KIND, "llm")
            llm_span._set_ctx_item(METADATA, {"key": "value"})
        tp = LLMObsTraceProcessor(llmobs_span_writer=mock_llmobs_span_writer)
        assert tp._llmobs_span_event(llm_span)[0]["meta"]["metadata"] == {"key": "value"}


def test_metrics_are_set():
    """Test that metadata is set on the span event if it is present on the span."""
    dummy_tracer = DummyTracer()
    mock_llmobs_span_writer = mock.MagicMock()
    with override_global_config(dict(_llmobs_ml_app="unnamed-ml-app")):
        with dummy_tracer.trace("root_llm_span", span_type=SpanTypes.LLM) as llm_span:
            llm_span._set_ctx_item(SPAN_KIND, "llm")
            llm_span._set_ctx_item(METRICS, {"tokens": 100})
        tp = LLMObsTraceProcessor(llmobs_span_writer=mock_llmobs_span_writer)
        assert tp._llmobs_span_event(llm_span)[0]["metrics"] == {"tokens": 100}


def test_langchain_span_name_is_set_to_class_name():
    """Test span names for langchain auto-instrumented spans is set correctly."""
    dummy_tracer = DummyTracer()
    mock_llmobs_span_writer = mock.MagicMock()
    with override_global_config(dict(_llmobs_ml_app="unnamed-ml-app")):
        with dummy_tracer.trace(LANGCHAIN_APM_SPAN_NAME, resource="expected_name", span_type=SpanTypes.LLM) as llm_span:
            llm_span._set_ctx_item(SPAN_KIND, "llm")
        tp = LLMObsTraceProcessor(llmobs_span_writer=mock_llmobs_span_writer)
        assert tp._llmobs_span_event(llm_span)[0]["name"] == "expected_name"


def test_error_is_set():
    """Test that error is set on the span event if it is present on the span."""
    dummy_tracer = DummyTracer()
    mock_llmobs_span_writer = mock.MagicMock()
    with override_global_config(dict(_llmobs_ml_app="unnamed-ml-app")):
        with pytest.raises(ValueError):
            with dummy_tracer.trace("root_llm_span", span_type=SpanTypes.LLM) as llm_span:
                llm_span._set_ctx_item(SPAN_KIND, "llm")
                raise ValueError("error")
        tp = LLMObsTraceProcessor(llmobs_span_writer=mock_llmobs_span_writer)
        span_event, _ = tp._llmobs_span_event(llm_span)
    assert span_event["meta"]["error.message"] == "error"
    assert "ValueError" in span_event["meta"]["error.type"]
    assert 'raise ValueError("error")' in span_event["meta"]["error.stack"]
