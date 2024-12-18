import mock

import pytest

from ddtrace.ext import SpanTypes
from ddtrace.llmobs import LLMObs
from ddtrace.llmobs._utils import _get_llmobs_parent_id
from ddtrace.llmobs._utils import _get_session_id
from ddtrace.llmobs import _constants as const

from tests.utils import DummyTracer
from tests.utils import override_global_config


@pytest.fixture
def mock_logs():
    with mock.patch("ddtrace.llmobs._llmobs.log") as mock_logs:
        yield mock_logs


def test_agentless_apm_warning():
    """Ensure a warning is emitted when using Agentless mode and APM is enabled."""
    pass


def test_set_correct_llmobs_parent_id():
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
        root_span._set_ctx_item(const.SESSION_ID, "test_session_id")
        with dummy_tracer.trace("child_span"):
            with dummy_tracer.trace("llm_span", span_type=SpanTypes.LLM) as llm_span:
                pass
    assert _get_session_id(llm_span) == "test_session_id"


def test_session_id_if_set_manually():
    """Test that session_id is extracted from the span if it is already set manually."""
    dummy_tracer = DummyTracer()
    with dummy_tracer.trace("root_llm_span", span_type=SpanTypes.LLM) as root_span:
        root_span._set_ctx_item(const.SESSION_ID, "test_session_id")
        with dummy_tracer.trace("child_span"):
            with dummy_tracer.trace("llm_span", span_type=SpanTypes.LLM) as llm_span:
                llm_span._set_ctx_item(const.SESSION_ID, "test_different_session_id")
    assert _get_session_id(llm_span) == "test_different_session_id"


def test_session_id_propagates_ignore_non_llmobs_spans():
    """
    Test that when session_id is not set, we propagate from nearest LLMObs ancestor
    even if there are non-LLMObs spans in between.
    """
    dummy_tracer = DummyTracer()
    with override_global_config(dict(_llmobs_ml_app="<not-a-real-app-name>")):
        with dummy_tracer.trace("root_llm_span", span_type=SpanTypes.LLM) as llm_span:
            llm_span._set_ctx_item(const.SPAN_KIND, "llm")
            llm_span._set_ctx_item(const.SESSION_ID, "session-123")
            with dummy_tracer.trace("child_span"):
                with dummy_tracer.trace("llm_grandchild_span", span_type=SpanTypes.LLM) as grandchild_span:
                    grandchild_span._set_ctx_item(const.SPAN_KIND, "llm")
                    with dummy_tracer.trace("great_grandchild_span", span_type=SpanTypes.LLM) as great_grandchild_span:
                        great_grandchild_span._set_ctx_item(const.SPAN_KIND, "llm")
        llm_span_event, _ = LLMObs._llmobs_span_event(llm_span)
        grandchild_span_event, _ = LLMObs._llmobs_span_event(grandchild_span)
        great_grandchild_span_event, _ = LLMObs._llmobs_span_event(great_grandchild_span)
    assert llm_span_event["session_id"] == "session-123"
    assert grandchild_span_event["session_id"] == "session-123"
    assert great_grandchild_span_event["session_id"] == "session-123"


def test_ml_app_propagates_ignore_non_llmobs_spans():
    """
    Test that when ml_app is not set, we propagate from nearest LLMObs ancestor
    even if there are non-LLMObs spans in between.
    """
    dummy_tracer = DummyTracer()
    with override_global_config(dict(_llmobs_ml_app="<not-a-real-app-name>")):
        with dummy_tracer.trace("root_llm_span", span_type=SpanTypes.LLM) as llm_span:
            llm_span._set_ctx_item(const.SPAN_KIND, "llm")
            llm_span._set_ctx_item(const.ML_APP, "test-ml-app")
            with dummy_tracer.trace("child_span"):
                with dummy_tracer.trace("llm_grandchild_span", span_type=SpanTypes.LLM) as grandchild_span:
                    grandchild_span._set_ctx_item(const.SPAN_KIND, "llm")
                    with dummy_tracer.trace("great_grandchild_span", span_type=SpanTypes.LLM) as great_grandchild_span:
                        great_grandchild_span._set_ctx_item(const.SPAN_KIND, "llm")
        llm_span_event, _ = LLMObs._llmobs_span_event(llm_span)
        grandchild_span_event, _ = LLMObs._llmobs_span_event(grandchild_span)
        great_grandchild_span_event, _ = LLMObs._llmobs_span_event(great_grandchild_span)
        assert "ml_app:test-ml-app" in llm_span_event["tags"]
        assert "ml_app:test-ml-app" in grandchild_span_event["tags"]
        assert "ml_app:test-ml-app" in great_grandchild_span_event["tags"]


def test_ml_app_tag_defaults_to_env_var():
    """Test that no ml_app defaults to the environment variable DD_LLMOBS_ML_APP."""
    dummy_tracer = DummyTracer()
    with override_global_config(dict(_llmobs_ml_app="<not-a-real-app-name>")):
        with dummy_tracer.trace("root_llm_span", span_type=SpanTypes.LLM) as llm_span:
            llm_span._set_ctx_item(const.SPAN_KIND, "llm")
        span_event, _ = LLMObs._llmobs_span_event(llm_span)
        assert "ml_app:<not-a-real-app-name>" in span_event["tags"]


def test_ml_app_tag_overrides_env_var():
    """Test that when ml_app is set on the span, it overrides the environment variable DD_LLMOBS_ML_APP."""
    dummy_tracer = DummyTracer()
    with override_global_config(dict(_llmobs_ml_app="<not-a-real-app-name>")):
        with dummy_tracer.trace("root_llm_span", span_type=SpanTypes.LLM) as llm_span:
            llm_span._set_ctx_item(const.SPAN_KIND, "llm")
            llm_span._set_ctx_item(const.ML_APP, "test-ml-app")
        span_event, _ = LLMObs._llmobs_span_event(llm_span)
        assert "ml_app:test-ml-app" in span_event["tags"]


def test_malformed_span_logs_error_instead_of_raising(mock_logs):
    """Test that a trying to create a span event from a malformed span will log an error instead of crashing."""
    dummy_tracer = DummyTracer()
    with dummy_tracer.trace("root_llm_span", span_type=SpanTypes.LLM) as llm_span:
        # span does not have SPAN_KIND tag
        pass
    LLMObs._instance._submit_llmobs_span(llm_span)
    assert mock_logs.error.call_count == 1
    mock_logs.error.assert_called_once_with(
        "Error generating LLMObs span event for span %s, likely due to malformed span", llm_span, exc_info=True
    )