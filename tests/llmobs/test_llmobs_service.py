import json

import mock
import pytest

from ddtrace.llmobs import LLMObs as llmobs_service
from ddtrace.llmobs._constants import INPUT_MESSAGES
from ddtrace.llmobs._constants import INPUT_PARAMETERS
from ddtrace.llmobs._constants import INPUT_VALUE
from ddtrace.llmobs._constants import METRICS
from ddtrace.llmobs._constants import MODEL_NAME
from ddtrace.llmobs._constants import MODEL_PROVIDER
from ddtrace.llmobs._constants import OUTPUT_MESSAGES
from ddtrace.llmobs._constants import OUTPUT_VALUE
from ddtrace.llmobs._constants import SESSION_ID
from ddtrace.llmobs._constants import SPAN_KIND
from ddtrace.llmobs._constants import TAGS
from ddtrace.llmobs._llmobs import LLMObsTraceProcessor
from tests.llmobs._utils import _expected_llmobs_llm_span_event
from tests.llmobs._utils import _expected_llmobs_non_llm_span_event
from tests.utils import DummyTracer
from tests.utils import override_global_config


@pytest.fixture
def mock_logs():
    with mock.patch("ddtrace.llmobs._llmobs.log") as mock_logs:
        yield mock_logs


def test_llmobs_service_enable():
    with override_global_config(dict(_dd_api_key="<not-a-real-api-key>", _llmobs_ml_app="<ml-app-name>")):
        dummy_tracer = DummyTracer()
        llmobs_service.enable(tracer=dummy_tracer)
        llmobs_instance = llmobs_service._instance
        assert llmobs_instance is not None
        assert llmobs_service.enabled
        assert llmobs_instance.tracer == dummy_tracer
        assert any(isinstance(tracer_filter, LLMObsTraceProcessor) for tracer_filter in dummy_tracer._filters)
        llmobs_service.disable()


def test_llmobs_service_disable():
    with override_global_config(dict(_dd_api_key="<not-a-real-api-key>", _llmobs_ml_app="<ml-app-name>")):
        dummy_tracer = DummyTracer()
        llmobs_service.enable(tracer=dummy_tracer)
        llmobs_service.disable()
        assert llmobs_service._instance is None
        assert llmobs_service.enabled is False


def test_llmobs_service_enable_no_api_key():
    with override_global_config(dict(_dd_api_key="", _llmobs_ml_app="<ml-app-name>")):
        dummy_tracer = DummyTracer()
        with pytest.raises(ValueError):
            llmobs_service.enable(tracer=dummy_tracer)
        llmobs_instance = llmobs_service._instance
        assert llmobs_instance is None
        assert llmobs_service.enabled is False


def test_llmobs_service_enable_no_ml_app_specified():
    with override_global_config(dict(_dd_api_key="<not-a-real-key>", _llmobs_ml_app="")):
        dummy_tracer = DummyTracer()
        with pytest.raises(ValueError):
            llmobs_service.enable(tracer=dummy_tracer)
        llmobs_instance = llmobs_service._instance
        assert llmobs_instance is None
        assert llmobs_service.enabled is False


def test_llmobs_service_enable_already_enabled(mock_logs):
    with override_global_config(dict(_dd_api_key="<not-a-real-api-key>", _llmobs_ml_app="<ml-app-name>")):
        dummy_tracer = DummyTracer()
        llmobs_service.enable(tracer=dummy_tracer)
        llmobs_service.enable(tracer=dummy_tracer)
        llmobs_instance = llmobs_service._instance
        assert llmobs_instance is not None
        assert llmobs_service.enabled
        assert llmobs_instance.tracer == dummy_tracer
        assert any(isinstance(tracer_filter, LLMObsTraceProcessor) for tracer_filter in dummy_tracer._filters)
        llmobs_service.disable()
        mock_logs.debug.assert_has_calls([mock.call("%s already enabled", "LLMObs")])


def test_llmobs_start_span_while_disabled_logs_warning(LLMObs, mock_logs):
    LLMObs.disable()
    _ = LLMObs.llm(model_name="test_model", name="test_llm_call", model_provider="test_provider")
    mock_logs.warning.assert_called_once_with("LLMObs.llm() cannot be used while LLMObs is disabled.")
    mock_logs.reset_mock()
    _ = LLMObs.tool(name="test_tool")
    mock_logs.warning.assert_called_once_with("LLMObs.tool() cannot be used while LLMObs is disabled.")
    mock_logs.reset_mock()
    _ = LLMObs.task(name="test_task")
    mock_logs.warning.assert_called_once_with("LLMObs.task() cannot be used while LLMObs is disabled.")
    mock_logs.reset_mock()
    _ = LLMObs.workflow(name="test_workflow")
    mock_logs.warning.assert_called_once_with("LLMObs.workflow() cannot be used while LLMObs is disabled.")
    mock_logs.reset_mock()
    _ = LLMObs.agent(name="test_agent")
    mock_logs.warning.assert_called_once_with("LLMObs.agent() cannot be used while LLMObs is disabled.")


def test_llmobs_start_span_uses_kind_as_default_name(LLMObs):
    with LLMObs.llm(model_name="test_model", model_provider="test_provider") as span:
        assert span.name == "llm"
    with LLMObs.tool() as span:
        assert span.name == "tool"
    with LLMObs.task() as span:
        assert span.name == "task"
    with LLMObs.workflow() as span:
        assert span.name == "workflow"
    with LLMObs.agent() as span:
        assert span.name == "agent"


def test_llmobs_start_span_with_session_id(LLMObs):
    with LLMObs.llm(model_name="test_model", session_id="test_session_id") as span:
        assert span.get_tag(SESSION_ID) == "test_session_id"
    with LLMObs.tool(session_id="test_session_id") as span:
        assert span.get_tag(SESSION_ID) == "test_session_id"
    with LLMObs.task(session_id="test_session_id") as span:
        assert span.get_tag(SESSION_ID) == "test_session_id"
    with LLMObs.workflow(session_id="test_session_id") as span:
        assert span.get_tag(SESSION_ID) == "test_session_id"
    with LLMObs.agent(session_id="test_session_id") as span:
        assert span.get_tag(SESSION_ID) == "test_session_id"


def test_llmobs_session_id_becomes_top_level_field(LLMObs, mock_llmobs_writer):
    session_id = "test_session_id"
    with LLMObs.task(session_id=session_id) as span:
        pass
    mock_llmobs_writer.enqueue.assert_called_with(
        _expected_llmobs_non_llm_span_event(span, "task", session_id=session_id)
    )


def test_llmobs_llm_span(LLMObs, mock_llmobs_writer):
    with LLMObs.llm(model_name="test_model", name="test_llm_call", model_provider="test_provider") as span:
        assert span.name == "test_llm_call"
        assert span.resource == "llm"
        assert span.span_type == "llm"
        assert span.get_tag(SPAN_KIND) == "llm"
        assert span.get_tag(MODEL_NAME) == "test_model"
        assert span.get_tag(MODEL_PROVIDER) == "test_provider"
        assert span.get_tag(SESSION_ID) is None

    mock_llmobs_writer.enqueue.assert_called_with(
        _expected_llmobs_llm_span_event(span, "llm", model_name="test_model", model_provider="test_provider")
    )


def test_llmobs_llm_span_no_model_raises_error(LLMObs, mock_logs):
    with pytest.raises(TypeError):
        with LLMObs.llm(name="test_llm_call", model_provider="test_provider"):
            pass


def test_llmobs_llm_span_empty_model_name_logs_warning(LLMObs, mock_logs):
    _ = LLMObs.llm(model_name="", name="test_llm_call", model_provider="test_provider")
    mock_logs.warning.assert_called_once_with("model_name must be the specified name of the invoked model.")


def test_llmobs_default_model_provider_set_to_custom(LLMObs):
    with LLMObs.llm(model_name="test_model", name="test_llm_call") as span:
        assert span.name == "test_llm_call"
        assert span.resource == "llm"
        assert span.span_type == "llm"
        assert span.get_tag(SPAN_KIND) == "llm"
        assert span.get_tag(MODEL_NAME) == "test_model"
        assert span.get_tag(MODEL_PROVIDER) == "custom"
        assert span.get_tag(SESSION_ID) is None


def test_llmobs_tool_span(LLMObs, mock_llmobs_writer):
    with LLMObs.tool(name="test_tool") as span:
        assert span.name == "test_tool"
        assert span.resource == "tool"
        assert span.span_type == "llm"
        assert span.get_tag(SPAN_KIND) == "tool"
        assert span.get_tag(SESSION_ID) is None
    mock_llmobs_writer.enqueue.assert_called_with(_expected_llmobs_non_llm_span_event(span, "tool"))


def test_llmobs_task_span(LLMObs, mock_llmobs_writer):
    with LLMObs.task(name="test_task") as span:
        assert span.name == "test_task"
        assert span.resource == "task"
        assert span.span_type == "llm"
        assert span.get_tag(SPAN_KIND) == "task"
        assert span.get_tag(SESSION_ID) is None
    mock_llmobs_writer.enqueue.assert_called_with(_expected_llmobs_non_llm_span_event(span, "task"))


def test_llmobs_workflow_span(LLMObs, mock_llmobs_writer):
    with LLMObs.workflow(name="test_workflow") as span:
        assert span.name == "test_workflow"
        assert span.resource == "workflow"
        assert span.span_type == "llm"
        assert span.get_tag(SPAN_KIND) == "workflow"
        assert span.get_tag(SESSION_ID) is None
    mock_llmobs_writer.enqueue.assert_called_with(_expected_llmobs_non_llm_span_event(span, "workflow"))


def test_llmobs_agent_span(LLMObs, mock_llmobs_writer):
    with LLMObs.agent(name="test_agent") as span:
        assert span.name == "test_agent"
        assert span.resource == "agent"
        assert span.span_type == "llm"
        assert span.get_tag(SPAN_KIND) == "agent"
        assert span.get_tag(SESSION_ID) is None
    mock_llmobs_writer.enqueue.assert_called_with(_expected_llmobs_llm_span_event(span, "agent"))


def test_llmobs_annotate_while_disabled_logs_warning(LLMObs, mock_logs):
    LLMObs.disable()
    LLMObs.annotate(parameters={"test": "test"})
    mock_logs.warning.assert_called_once_with("LLMObs.annotate() cannot be used while LLMObs is disabled.")


def test_llmobs_annotate_no_active_span_logs_warning(LLMObs, mock_logs):
    LLMObs.annotate(parameters={"test": "test"})
    mock_logs.warning.assert_called_once_with("No span provided and no active span found.")


def test_llmobs_annotate_non_llm_span_logs_warning(LLMObs, mock_logs):
    dummy_tracer = DummyTracer()
    with dummy_tracer.trace("root") as non_llmobs_span:
        LLMObs.annotate(span=non_llmobs_span, parameters={"test": "test"})
        mock_logs.warning.assert_called_once_with("Span must be an LLM-type span.")


def test_llmobs_annotate_finished_span_does_nothing(LLMObs, mock_logs):
    with LLMObs.llm(model_name="test_model", name="test_llm_call", model_provider="test_provider") as span:
        pass
    LLMObs.annotate(span=span, parameters={"test": "test"})
    mock_logs.warning.assert_called_once_with("Cannot annotate a finished span.")


def test_llmobs_annotate_parameters(LLMObs):
    with LLMObs.llm(model_name="test_model", name="test_llm_call", model_provider="test_provider") as span:
        LLMObs.annotate(span=span, parameters={"temperature": 0.9, "max_tokens": 50})
        assert json.loads(span.get_tag(INPUT_PARAMETERS)) == {"temperature": 0.9, "max_tokens": 50}


def test_llmobs_annotate_tag(LLMObs):
    with LLMObs.llm(model_name="test_model", name="test_llm_call", model_provider="test_provider") as span:
        LLMObs.annotate(span=span, tags={"test_tag_name": "test_tag_value", "test_numeric_tag": 10})
        assert json.loads(span.get_tag(TAGS)) == {"test_tag_name": "test_tag_value", "test_numeric_tag": 10}


def test_llmobs_annotate_input_string(LLMObs):
    with LLMObs.llm(model_name="test_model") as llm_span:
        LLMObs.annotate(span=llm_span, input_data="test_input")
        assert json.loads(llm_span.get_tag(INPUT_MESSAGES)) == [{"content": "test_input"}]
    with LLMObs.task() as task_span:
        LLMObs.annotate(span=task_span, input_data="test_input")
        assert task_span.get_tag(INPUT_VALUE) == "test_input"
    with LLMObs.tool() as tool_span:
        LLMObs.annotate(span=tool_span, input_data="test_input")
        assert tool_span.get_tag(INPUT_VALUE) == "test_input"
    with LLMObs.workflow() as workflow_span:
        LLMObs.annotate(span=workflow_span, input_data="test_input")
        assert workflow_span.get_tag(INPUT_VALUE) == "test_input"
    with LLMObs.agent() as agent_span:
        LLMObs.annotate(span=agent_span, input_data="test_input")
        assert json.loads(agent_span.get_tag(INPUT_MESSAGES)) == [{"content": "test_input"}]


def test_llmobs_annotate_input_llm_message(LLMObs):
    with LLMObs.llm(model_name="test_model") as llm_span:
        LLMObs.annotate(span=llm_span, input_data=[{"content": "test_input", "role": "human"}])
        assert json.loads(llm_span.get_tag(INPUT_MESSAGES)) == [{"content": "test_input", "role": "human"}]


def test_llmobs_annotate_non_llm_span_message_input_logs_warning(LLMObs, mock_logs):
    with LLMObs.task() as span:
        LLMObs.annotate(span=span, input_data=[{"content": "test_input"}])
        mock_logs.warning.assert_called_once_with("Invalid input/output type for non-llm span. Must be a raw string.")


def test_llmobs_annotate_output_string(LLMObs):
    with LLMObs.llm(model_name="test_model") as llm_span:
        LLMObs.annotate(span=llm_span, output_data="test_output")
        assert json.loads(llm_span.get_tag(OUTPUT_MESSAGES)) == [{"content": "test_output"}]
    with LLMObs.task() as task_span:
        LLMObs.annotate(span=task_span, output_data="test_output")
        assert task_span.get_tag(OUTPUT_VALUE) == "test_output"
    with LLMObs.tool() as tool_span:
        LLMObs.annotate(span=tool_span, output_data="test_output")
        assert tool_span.get_tag(OUTPUT_VALUE) == "test_output"
    with LLMObs.workflow() as workflow_span:
        LLMObs.annotate(span=workflow_span, output_data="test_output")
        assert workflow_span.get_tag(OUTPUT_VALUE) == "test_output"
    with LLMObs.agent() as agent_span:
        LLMObs.annotate(span=agent_span, output_data="test_output")
        assert json.loads(agent_span.get_tag(OUTPUT_MESSAGES)) == [{"content": "test_output"}]


def test_llmobs_annotate_output_llm_message(LLMObs):
    with LLMObs.llm(model_name="test_model") as llm_span:
        LLMObs.annotate(span=llm_span, output_data=[{"content": "test_output", "role": "human"}])
        assert json.loads(llm_span.get_tag(OUTPUT_MESSAGES)) == [{"content": "test_output", "role": "human"}]


def test_llmobs_annotate_non_llm_span_message_output_logs_warning(LLMObs, mock_logs):
    with LLMObs.task() as span:
        LLMObs.annotate(span=span, output_data=[{"content": "test_input"}])
        mock_logs.warning.assert_called_once_with("Invalid input/output type for non-llm span. Must be a raw string.")


def test_llmobs_annotate_metrics(LLMObs):
    with LLMObs.llm(model_name="test_model") as span:
        LLMObs.annotate(span=span, metrics={"prompt_tokens": 10, "completion_tokens": 20, "total_tokens": 30})
        assert json.loads(span.get_tag(METRICS)) == {"prompt_tokens": 10, "completion_tokens": 20, "total_tokens": 30}


def test_llmobs_span_error_sets_error(LLMObs, mock_llmobs_writer):
    with pytest.raises(ValueError):
        with LLMObs.llm(model_name="test_model", model_provider="test_model_provider") as span:
            raise ValueError("test error message")
    mock_llmobs_writer.enqueue.assert_called_with(
        _expected_llmobs_llm_span_event(
            span,
            model_name="test_model",
            model_provider="test_model_provider",
            error="builtins.ValueError",
            error_message="test error message",
            error_stack=span.get_tag("error.stack"),
        )
    )


@pytest.mark.parametrize(
    "ddtrace_global_config",
    [dict(version="1.2.3", env="test_env", service="test_service", _llmobs_ml_app="test_app_name")],
)
def test_llmobs_tags(ddtrace_global_config, LLMObs, mock_llmobs_writer, monkeypatch):
    with LLMObs.task(name="test_task") as span:
        pass
    mock_llmobs_writer.enqueue.assert_called_with(
        _expected_llmobs_non_llm_span_event(
            span,
            "task",
            tags={"version": "1.2.3", "env": "test_env", "service": "test_service", "ml_app": "test_app_name"},
        )
    )


def test_llmobs_ml_app_override(LLMObs, mock_llmobs_writer):
    with LLMObs.task(name="test_task", ml_app="test_app") as span:
        pass
    mock_llmobs_writer.enqueue.assert_called_with(
        _expected_llmobs_non_llm_span_event(span, "task", tags={"ml_app": "test_app"})
    )

    with LLMObs.tool(name="test_tool", ml_app="test_app") as span:
        pass
    mock_llmobs_writer.enqueue.assert_called_with(
        _expected_llmobs_non_llm_span_event(span, "tool", tags={"ml_app": "test_app"})
    )

    with LLMObs.llm(model_name="model_name", name="test_llm", ml_app="test_app") as span:
        pass
    mock_llmobs_writer.enqueue.assert_called_with(
        _expected_llmobs_llm_span_event(
            span, "llm", model_name="model_name", model_provider="custom", tags={"ml_app": "test_app"}
        )
    )

    with LLMObs.workflow(name="test_workflow", ml_app="test_app") as span:
        pass
    mock_llmobs_writer.enqueue.assert_called_with(
        _expected_llmobs_non_llm_span_event(span, "workflow", tags={"ml_app": "test_app"})
    )

    with LLMObs.agent(name="test_agent", ml_app="test_app") as span:
        pass
    mock_llmobs_writer.enqueue.assert_called_with(
        _expected_llmobs_llm_span_event(span, "agent", tags={"ml_app": "test_app"})
    )
