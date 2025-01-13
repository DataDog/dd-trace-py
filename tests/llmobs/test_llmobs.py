import pytest

from ddtrace.ext import SpanTypes
from ddtrace.llmobs import _constants as const
from ddtrace.llmobs._utils import _get_llmobs_parent_id
from ddtrace.llmobs._utils import _get_session_id
from tests.llmobs._utils import _expected_llmobs_llm_span_event


class TestMLApp:
    @pytest.mark.parametrize("llmobs_env", [{"DD_LLMOBS_ML_APP": "<not-a-real-app-name>"}])
    def test_tag_defaults_to_env_var(self, tracer, llmobs_env, llmobs_events):
        """Test that no ml_app defaults to the environment variable DD_LLMOBS_ML_APP."""
        with tracer.trace("root_llm_span", span_type=SpanTypes.LLM) as llm_span:
            llm_span._set_ctx_item(const.SPAN_KIND, "llm")
        assert "ml_app:<not-a-real-app-name>" in llmobs_events[0]["tags"]

    @pytest.mark.parametrize("llmobs_env", [{"DD_LLMOBS_ML_APP": "<not-a-real-app-name>"}])
    def test_tag_overrides_env_var(self, tracer, llmobs_env, llmobs_events):
        """Test that when ml_app is set on the span, it overrides the environment variable DD_LLMOBS_ML_APP."""
        with tracer.trace("root_llm_span", span_type=SpanTypes.LLM) as llm_span:
            llm_span._set_ctx_item(const.SPAN_KIND, "llm")
            llm_span._set_ctx_item(const.ML_APP, "test-ml-app")
        assert "ml_app:test-ml-app" in llmobs_events[0]["tags"]

    def test_propagates_ignore_non_llmobs_spans(self, tracer, llmobs_events):
        """
        Test that when ml_app is not set, we propagate from nearest LLMObs ancestor
        even if there are non-LLMObs spans in between.
        """
        with tracer.trace("root_llm_span", span_type=SpanTypes.LLM) as llm_span:
            llm_span._set_ctx_item(const.SPAN_KIND, "llm")
            llm_span._set_ctx_item(const.ML_APP, "test-ml-app")
            with tracer.trace("child_span"):
                with tracer.trace("llm_grandchild_span", span_type=SpanTypes.LLM) as grandchild_span:
                    grandchild_span._set_ctx_item(const.SPAN_KIND, "llm")
                    with tracer.trace("great_grandchild_span", span_type=SpanTypes.LLM) as great_grandchild_span:
                        great_grandchild_span._set_ctx_item(const.SPAN_KIND, "llm")
        assert len(llmobs_events) == 3
        for llmobs_event in llmobs_events:
            assert "ml_app:test-ml-app" in llmobs_event["tags"]


def test_set_correct_parent_id(tracer):
    """Test that the parent_id is set as the span_id of the nearest LLMObs span in the span's ancestor tree."""
    with tracer.trace("root"):
        with tracer.trace("llm_span", span_type=SpanTypes.LLM) as llm_span:
            pass
    assert _get_llmobs_parent_id(llm_span) is None
    with tracer.trace("root_llm_span", span_type=SpanTypes.LLM) as root_span:
        with tracer.trace("child_span") as child_span:
            with tracer.trace("llm_span", span_type=SpanTypes.LLM) as grandchild_span:
                pass
    assert _get_llmobs_parent_id(root_span) is None
    assert _get_llmobs_parent_id(child_span) == str(root_span.span_id)
    assert _get_llmobs_parent_id(grandchild_span) == str(root_span.span_id)


class TestSessionId:
    def test_propagate_from_ancestors(self, tracer):
        """
        Test that session_id is propagated from the nearest LLMObs span in the span's ancestor tree
        if no session_id is not set on the span itself.
        """
        with tracer.trace("root_llm_span", span_type=SpanTypes.LLM) as root_span:
            root_span._set_ctx_item(const.SESSION_ID, "test_session_id")
            with tracer.trace("child_span"):
                with tracer.trace("llm_span", span_type=SpanTypes.LLM) as llm_span:
                    pass
        assert _get_session_id(llm_span) == "test_session_id"

    def test_if_set_manually(self, tracer):
        """Test that session_id is extracted from the span if it is already set manually."""
        with tracer.trace("root_llm_span", span_type=SpanTypes.LLM) as root_span:
            root_span._set_ctx_item(const.SESSION_ID, "test_session_id")
            with tracer.trace("child_span"):
                with tracer.trace("llm_span", span_type=SpanTypes.LLM) as llm_span:
                    llm_span._set_ctx_item(const.SESSION_ID, "test_different_session_id")
        assert _get_session_id(llm_span) == "test_different_session_id"

    def test_propagates_ignore_non_llmobs_spans(self, tracer, llmobs_events):
        """
        Test that when session_id is not set, we propagate from nearest LLMObs ancestor
        even if there are non-LLMObs spans in between.
        """
        with tracer.trace("root_llm_span", span_type=SpanTypes.LLM) as llm_span:
            llm_span._set_ctx_item(const.SPAN_KIND, "llm")
            llm_span._set_ctx_item(const.SESSION_ID, "session-123")
            with tracer.trace("child_span"):
                with tracer.trace("llm_grandchild_span", span_type=SpanTypes.LLM) as grandchild_span:
                    grandchild_span._set_ctx_item(const.SPAN_KIND, "llm")
                    with tracer.trace("great_grandchild_span", span_type=SpanTypes.LLM) as great_grandchild_span:
                        great_grandchild_span._set_ctx_item(const.SPAN_KIND, "llm")

        llm_event, grandchild_event, great_grandchild_event = llmobs_events
        assert llm_event["session_id"] == "session-123"
        assert grandchild_event["session_id"] == "session-123"
        assert great_grandchild_event["session_id"] == "session-123"


def test_input_value_is_set(tracer, llmobs_events):
    """Test that input value is set on the span event if they are present on the span."""
    with tracer.trace("root_llm_span", span_type=SpanTypes.LLM) as llm_span:
        llm_span._set_ctx_item(const.SPAN_KIND, "llm")
        llm_span._set_ctx_item(const.INPUT_VALUE, "value")
    assert llmobs_events[0]["meta"]["input"]["value"] == "value"


def test_input_messages_are_set(tracer, llmobs_events):
    """Test that input messages are set on the span event if they are present on the span."""
    with tracer.trace("root_llm_span", span_type=SpanTypes.LLM) as llm_span:
        llm_span._set_ctx_item(const.SPAN_KIND, "llm")
        llm_span._set_ctx_item(const.INPUT_MESSAGES, [{"content": "message", "role": "user"}])
    assert llmobs_events[0]["meta"]["input"]["messages"] == [{"content": "message", "role": "user"}]


def test_input_parameters_are_set(tracer, llmobs_events):
    """Test that input parameters are set on the span event if they are present on the span."""
    with tracer.trace("root_llm_span", span_type=SpanTypes.LLM) as llm_span:
        llm_span._set_ctx_item(const.SPAN_KIND, "llm")
        llm_span._set_ctx_item(const.INPUT_PARAMETERS, {"key": "value"})
    assert llmobs_events[0]["meta"]["input"]["parameters"] == {"key": "value"}


def test_output_messages_are_set(tracer, llmobs_events):
    """Test that output messages are set on the span event if they are present on the span."""
    with tracer.trace("root_llm_span", span_type=SpanTypes.LLM) as llm_span:
        llm_span._set_ctx_item(const.SPAN_KIND, "llm")
        llm_span._set_ctx_item(const.OUTPUT_MESSAGES, [{"content": "message", "role": "user"}])
    assert llmobs_events[0]["meta"]["output"]["messages"] == [{"content": "message", "role": "user"}]


def test_output_value_is_set(tracer, llmobs_events):
    """Test that output value is set on the span event if they are present on the span."""
    with tracer.trace("root_llm_span", span_type=SpanTypes.LLM) as llm_span:
        llm_span._set_ctx_item(const.SPAN_KIND, "llm")
        llm_span._set_ctx_item(const.OUTPUT_VALUE, "value")
    assert llmobs_events[0]["meta"]["output"]["value"] == "value"


def test_prompt_is_set(tracer, llmobs_events):
    """Test that prompt is set on the span event if they are present on the span."""
    with tracer.trace("root_llm_span", span_type=SpanTypes.LLM) as llm_span:
        llm_span._set_ctx_item(const.SPAN_KIND, "llm")
        llm_span._set_ctx_item(const.INPUT_PROMPT, {"variables": {"var1": "var2"}})
    assert llmobs_events[0]["meta"]["input"]["prompt"] == {"variables": {"var1": "var2"}}


def test_prompt_is_not_set_for_non_llm_spans(tracer, llmobs_events):
    """Test that prompt is NOT set on the span event if the span is not an LLM span."""
    with tracer.trace("task_span", span_type=SpanTypes.LLM) as task_span:
        task_span._set_ctx_item(const.SPAN_KIND, "task")
        task_span._set_ctx_item(const.INPUT_VALUE, "ival")
        task_span._set_ctx_item(const.INPUT_PROMPT, {"variables": {"var1": "var2"}})
    assert llmobs_events[0]["meta"]["input"].get("prompt") is None


def test_metadata_is_set(tracer, llmobs_events):
    """Test that metadata is set on the span event if it is present on the span."""
    with tracer.trace("root_llm_span", span_type=SpanTypes.LLM) as llm_span:
        llm_span._set_ctx_item(const.SPAN_KIND, "llm")
        llm_span._set_ctx_item(const.METADATA, {"key": "value"})
    assert llmobs_events[0]["meta"]["metadata"] == {"key": "value"}


def test_metrics_are_set(tracer, llmobs_events):
    """Test that metadata is set on the span event if it is present on the span."""
    with tracer.trace("root_llm_span", span_type=SpanTypes.LLM) as llm_span:
        llm_span._set_ctx_item(const.SPAN_KIND, "llm")
        llm_span._set_ctx_item(const.METRICS, {"tokens": 100})
    assert llmobs_events[0]["metrics"] == {"tokens": 100}


def test_langchain_span_name_is_set_to_class_name(tracer, llmobs_events):
    """Test span names for langchain auto-instrumented spans is set correctly."""
    with tracer.trace(const.LANGCHAIN_APM_SPAN_NAME, resource="expected_name", span_type=SpanTypes.LLM) as llm_span:
        llm_span._set_ctx_item(const.SPAN_KIND, "llm")
    assert llmobs_events[0]["name"] == "expected_name"


def test_error_is_set(tracer, llmobs_events):
    """Test that error is set on the span event if it is present on the span."""
    with pytest.raises(ValueError):
        with tracer.trace("root_llm_span", span_type=SpanTypes.LLM) as llm_span:
            llm_span._set_ctx_item(const.SPAN_KIND, "llm")
            raise ValueError("error")
    span_event = llmobs_events[0]
    assert span_event["meta"]["error.message"] == "error"
    assert "ValueError" in span_event["meta"]["error.type"]
    assert 'raise ValueError("error")' in span_event["meta"]["error.stack"]


def test_model_provider_defaults_to_custom(tracer, llmobs_events):
    """Test that model provider defaults to "custom" if not provided."""
    with tracer.trace("root_llm_span", span_type=SpanTypes.LLM) as llm_span:
        llm_span._set_ctx_item(const.SPAN_KIND, "llm")
        llm_span._set_ctx_item(const.MODEL_NAME, "model_name")
    span_event = llmobs_events[0]
    assert span_event["meta"]["model_name"] == "model_name"
    assert span_event["meta"]["model_provider"] == "custom"


def test_model_not_set_if_not_llm_kind_span(tracer, llmobs_events):
    """Test that model name and provider not set if non-LLM span."""
    with tracer.trace("root_workflow_span", span_type=SpanTypes.LLM) as span:
        span._set_ctx_item(const.SPAN_KIND, "workflow")
        span._set_ctx_item(const.MODEL_NAME, "model_name")
    span_event = llmobs_events[0]
    assert "model_name" not in span_event["meta"]
    assert "model_provider" not in span_event["meta"]


def test_model_and_provider_are_set(tracer, llmobs_events):
    """Test that model and provider are set on the span event if they are present on the LLM-kind span."""
    with tracer.trace("root_llm_span", span_type=SpanTypes.LLM) as llm_span:
        llm_span._set_ctx_item(const.SPAN_KIND, "llm")
        llm_span._set_ctx_item(const.MODEL_NAME, "model_name")
        llm_span._set_ctx_item(const.MODEL_PROVIDER, "model_provider")
    span_event = llmobs_events[0]
    assert span_event["meta"]["model_name"] == "model_name"
    assert span_event["meta"]["model_provider"] == "model_provider"


def test_malformed_span_logs_error_instead_of_raising(tracer, llmobs_events, mock_llmobs_logs):
    """Test that a trying to create a span event from a malformed span will log an error instead of crashing."""
    with tracer.trace("root_llm_span", span_type=SpanTypes.LLM) as llm_span:
        # span does not have SPAN_KIND tag
        pass
    mock_llmobs_logs.error.assert_called_with(
        "Error generating LLMObs span event for span %s, likely due to malformed span", llm_span, exc_info=True
    )
    assert len(llmobs_events) == 0


def test_only_generate_span_events_from_llmobs_spans(tracer, llmobs_events):
    """Test that we only generate LLMObs span events for LLM span types."""
    with tracer.trace("root_llm_span", service="tests.llmobs", span_type=SpanTypes.LLM) as root_span:
        root_span._set_ctx_item(const.SPAN_KIND, "llm")
        with tracer.trace("child_span"):
            with tracer.trace("llm_span", span_type=SpanTypes.LLM) as grandchild_span:
                grandchild_span._set_ctx_item(const.SPAN_KIND, "llm")
    expected_grandchild_llmobs_span = _expected_llmobs_llm_span_event(grandchild_span, "llm")
    expected_grandchild_llmobs_span["parent_id"] = str(root_span.span_id)

    assert len(llmobs_events) == 2
    assert llmobs_events[1] == _expected_llmobs_llm_span_event(root_span, "llm")
    assert llmobs_events[0] == expected_grandchild_llmobs_span
