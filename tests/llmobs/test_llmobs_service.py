import json
import os

import mock
import pytest

import ddtrace
from ddtrace._trace.context import Context
from ddtrace._trace.span import Span
from ddtrace.ext import SpanTypes
from ddtrace.filters import TraceFilter
from ddtrace.internal.service import ServiceStatus
from ddtrace.llmobs import LLMObs as llmobs_service
from ddtrace.llmobs._constants import INPUT_DOCUMENTS
from ddtrace.llmobs._constants import INPUT_MESSAGES
from ddtrace.llmobs._constants import INPUT_PARAMETERS
from ddtrace.llmobs._constants import INPUT_VALUE
from ddtrace.llmobs._constants import METADATA
from ddtrace.llmobs._constants import METRICS
from ddtrace.llmobs._constants import MODEL_NAME
from ddtrace.llmobs._constants import MODEL_PROVIDER
from ddtrace.llmobs._constants import OUTPUT_DOCUMENTS
from ddtrace.llmobs._constants import OUTPUT_MESSAGES
from ddtrace.llmobs._constants import OUTPUT_VALUE
from ddtrace.llmobs._constants import PROPAGATED_PARENT_ID_KEY
from ddtrace.llmobs._constants import SESSION_ID
from ddtrace.llmobs._constants import SPAN_KIND
from ddtrace.llmobs._constants import SPAN_START_WHILE_DISABLED_WARNING
from ddtrace.llmobs._constants import TAGS
from ddtrace.llmobs._llmobs import LLMObsTraceProcessor
from tests.llmobs._utils import _expected_llmobs_eval_metric_event
from tests.llmobs._utils import _expected_llmobs_llm_span_event
from tests.llmobs._utils import _expected_llmobs_non_llm_span_event
from tests.utils import DummyTracer
from tests.utils import override_global_config


@pytest.fixture
def mock_logs():
    with mock.patch("ddtrace.llmobs._llmobs.log") as mock_logs:
        yield mock_logs


def run_llmobs_trace_filter(dummy_tracer):
    for trace_filter in dummy_tracer._filters:
        if isinstance(trace_filter, LLMObsTraceProcessor):
            root_llm_span = Span(name="span1", span_type=SpanTypes.LLM)
            root_llm_span.set_tag_str(SPAN_KIND, "llm")
            trace1 = [root_llm_span]
            return trace_filter.process_trace(trace1)
    raise ValueError("LLMObsTraceProcessor not found in tracer filters.")


def test_service_enable():
    with override_global_config(dict(_dd_api_key="<not-a-real-api-key>", _llmobs_ml_app="<ml-app-name>")):
        dummy_tracer = DummyTracer()
        llmobs_service.enable(_tracer=dummy_tracer)
        llmobs_instance = llmobs_service._instance
        assert llmobs_instance is not None
        assert llmobs_service.enabled
        assert llmobs_instance.tracer == dummy_tracer
        assert any(isinstance(tracer_filter, LLMObsTraceProcessor) for tracer_filter in dummy_tracer._filters)
        assert run_llmobs_trace_filter(dummy_tracer) is not None

        llmobs_service.disable()


def test_service_enable_with_apm_disabled(monkeypatch):
    with override_global_config(dict(_dd_api_key="<not-a-real-api-key>", _llmobs_ml_app="<ml-app-name>")):
        dummy_tracer = DummyTracer()
        llmobs_service.enable(_tracer=dummy_tracer, agentless_enabled=True)
        llmobs_instance = llmobs_service._instance
        assert llmobs_instance is not None
        assert llmobs_service.enabled
        assert llmobs_instance.tracer == dummy_tracer
        assert any(isinstance(tracer_filter, LLMObsTraceProcessor) for tracer_filter in dummy_tracer._filters)
        assert run_llmobs_trace_filter(dummy_tracer) is None

        llmobs_service.disable()


def test_service_disable():
    with override_global_config(dict(_dd_api_key="<not-a-real-api-key>", _llmobs_ml_app="<ml-app-name>")):
        dummy_tracer = DummyTracer()
        llmobs_service.enable(_tracer=dummy_tracer)
        llmobs_service.disable()
        assert llmobs_service.enabled is False
        assert llmobs_service._instance._llmobs_eval_metric_writer.status.value == "stopped"
        assert llmobs_service._instance._llmobs_span_writer.status.value == "stopped"


def test_service_enable_no_api_key():
    with override_global_config(dict(_dd_api_key="", _llmobs_ml_app="<ml-app-name>")):
        dummy_tracer = DummyTracer()
        with pytest.raises(ValueError):
            llmobs_service.enable(_tracer=dummy_tracer, agentless_enabled=True)
        assert llmobs_service.enabled is False
        assert llmobs_service._instance._llmobs_eval_metric_writer.status.value == "stopped"
        assert llmobs_service._instance._llmobs_span_writer.status.value == "stopped"


def test_service_enable_no_ml_app_specified():
    with override_global_config(dict(_dd_api_key="<not-a-real-key>", _llmobs_ml_app="")):
        dummy_tracer = DummyTracer()
        with pytest.raises(ValueError):
            llmobs_service.enable(_tracer=dummy_tracer)
        assert llmobs_service.enabled is False
        assert llmobs_service._instance._llmobs_eval_metric_writer.status.value == "stopped"
        assert llmobs_service._instance._llmobs_span_writer.status.value == "stopped"


def test_service_enable_deprecated_ml_app_name(monkeypatch, mock_logs):
    with override_global_config(dict(_dd_api_key="<not-a-real-key>", _llmobs_ml_app="")):
        dummy_tracer = DummyTracer()
        monkeypatch.setenv("DD_LLMOBS_APP_NAME", "test_ml_app")
        llmobs_service.enable(_tracer=dummy_tracer)
        assert llmobs_service.enabled is True
        assert llmobs_service._instance._llmobs_eval_metric_writer.status.value == "running"
        assert llmobs_service._instance._llmobs_span_writer.status.value == "running"
        mock_logs.warning.assert_called_once_with("`DD_LLMOBS_APP_NAME` is deprecated. Use `DD_LLMOBS_ML_APP` instead.")
        llmobs_service.disable()


def test_service_enable_already_enabled(mock_logs):
    with override_global_config(dict(_dd_api_key="<not-a-real-api-key>", _llmobs_ml_app="<ml-app-name>")):
        dummy_tracer = DummyTracer()
        llmobs_service.enable(_tracer=dummy_tracer)
        llmobs_service.enable(_tracer=dummy_tracer)
        llmobs_instance = llmobs_service._instance
        assert llmobs_instance is not None
        assert llmobs_service.enabled
        assert llmobs_instance.tracer == dummy_tracer
        assert any(isinstance(tracer_filter, LLMObsTraceProcessor) for tracer_filter in dummy_tracer._filters)
        llmobs_service.disable()
        mock_logs.debug.assert_has_calls([mock.call("%s already enabled", "LLMObs")])


def test_start_span_while_disabled_logs_warning(LLMObs, mock_logs):
    LLMObs.disable()
    _ = LLMObs.llm(model_name="test_model", name="test_llm_call", model_provider="test_provider")
    mock_logs.warning.assert_called_once_with(SPAN_START_WHILE_DISABLED_WARNING)
    mock_logs.reset_mock()
    _ = LLMObs.tool(name="test_tool")
    mock_logs.warning.assert_called_once_with(SPAN_START_WHILE_DISABLED_WARNING)
    mock_logs.reset_mock()
    _ = LLMObs.task(name="test_task")
    mock_logs.warning.assert_called_once_with(SPAN_START_WHILE_DISABLED_WARNING)
    mock_logs.reset_mock()
    _ = LLMObs.workflow(name="test_workflow")
    mock_logs.warning.assert_called_once_with(SPAN_START_WHILE_DISABLED_WARNING)
    mock_logs.reset_mock()
    _ = LLMObs.agent(name="test_agent")
    mock_logs.warning.assert_called_once_with(SPAN_START_WHILE_DISABLED_WARNING)


def test_start_span_uses_kind_as_default_name(LLMObs):
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


def test_start_span_with_session_id(LLMObs):
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


def test_session_id_becomes_top_level_field(LLMObs, mock_llmobs_span_writer):
    session_id = "test_session_id"
    with LLMObs.task(session_id=session_id) as span:
        pass
    mock_llmobs_span_writer.enqueue.assert_called_with(
        _expected_llmobs_non_llm_span_event(span, "task", session_id=session_id)
    )


def test_session_id_becomes_top_level_field_agentless(AgentlessLLMObs, mock_llmobs_span_agentless_writer):
    session_id = "test_session_id"
    with AgentlessLLMObs.task(session_id=session_id) as span:
        pass
    mock_llmobs_span_agentless_writer.enqueue.assert_called_with(
        _expected_llmobs_non_llm_span_event(span, "task", session_id=session_id)
    )


def test_llm_span(LLMObs, mock_llmobs_span_writer):
    with LLMObs.llm(model_name="test_model", name="test_llm_call", model_provider="test_provider") as span:
        assert span.name == "test_llm_call"
        assert span.resource == "llm"
        assert span.span_type == "llm"
        assert span.get_tag(SPAN_KIND) == "llm"
        assert span.get_tag(MODEL_NAME) == "test_model"
        assert span.get_tag(MODEL_PROVIDER) == "test_provider"

    mock_llmobs_span_writer.enqueue.assert_called_with(
        _expected_llmobs_llm_span_event(span, "llm", model_name="test_model", model_provider="test_provider")
    )


def test_llm_span_agentless(AgentlessLLMObs, mock_llmobs_span_agentless_writer):
    with AgentlessLLMObs.llm(model_name="test_model", name="test_llm_call", model_provider="test_provider") as span:
        assert span.name == "test_llm_call"
        assert span.resource == "llm"
        assert span.span_type == "llm"
        assert span.get_tag(SPAN_KIND) == "llm"
        assert span.get_tag(MODEL_NAME) == "test_model"
        assert span.get_tag(MODEL_PROVIDER) == "test_provider"

    mock_llmobs_span_agentless_writer.enqueue.assert_called_with(
        _expected_llmobs_llm_span_event(span, "llm", model_name="test_model", model_provider="test_provider")
    )


def test_llm_span_no_model_raises_error(LLMObs, mock_logs):
    with pytest.raises(TypeError):
        with LLMObs.llm(name="test_llm_call", model_provider="test_provider"):
            pass


def test_llm_span_empty_model_name_logs_warning(LLMObs, mock_logs):
    _ = LLMObs.llm(model_name="", name="test_llm_call", model_provider="test_provider")
    mock_logs.warning.assert_called_once_with("LLMObs.llm() missing model_name")


def test_default_model_provider_set_to_custom(LLMObs):
    with LLMObs.llm(model_name="test_model", name="test_llm_call") as span:
        assert span.name == "test_llm_call"
        assert span.resource == "llm"
        assert span.span_type == "llm"
        assert span.get_tag(SPAN_KIND) == "llm"
        assert span.get_tag(MODEL_NAME) == "test_model"
        assert span.get_tag(MODEL_PROVIDER) == "custom"


def test_tool_span(LLMObs, mock_llmobs_span_writer):
    with LLMObs.tool(name="test_tool") as span:
        assert span.name == "test_tool"
        assert span.resource == "tool"
        assert span.span_type == "llm"
        assert span.get_tag(SPAN_KIND) == "tool"
    mock_llmobs_span_writer.enqueue.assert_called_with(_expected_llmobs_non_llm_span_event(span, "tool"))


def test_tool_span_agentless(AgentlessLLMObs, mock_llmobs_span_agentless_writer):
    with AgentlessLLMObs.tool(name="test_tool") as span:
        assert span.name == "test_tool"
        assert span.resource == "tool"
        assert span.span_type == "llm"
        assert span.get_tag(SPAN_KIND) == "tool"
    mock_llmobs_span_agentless_writer.enqueue.assert_called_with(_expected_llmobs_non_llm_span_event(span, "tool"))


def test_task_span(LLMObs, mock_llmobs_span_writer):
    with LLMObs.task(name="test_task") as span:
        assert span.name == "test_task"
        assert span.resource == "task"
        assert span.span_type == "llm"
        assert span.get_tag(SPAN_KIND) == "task"
    mock_llmobs_span_writer.enqueue.assert_called_with(_expected_llmobs_non_llm_span_event(span, "task"))


def test_task_span_agentless(AgentlessLLMObs, mock_llmobs_span_agentless_writer):
    with AgentlessLLMObs.task(name="test_task") as span:
        assert span.name == "test_task"
        assert span.resource == "task"
        assert span.span_type == "llm"
        assert span.get_tag(SPAN_KIND) == "task"
    mock_llmobs_span_agentless_writer.enqueue.assert_called_with(_expected_llmobs_non_llm_span_event(span, "task"))


def test_workflow_span(LLMObs, mock_llmobs_span_writer):
    with LLMObs.workflow(name="test_workflow") as span:
        assert span.name == "test_workflow"
        assert span.resource == "workflow"
        assert span.span_type == "llm"
        assert span.get_tag(SPAN_KIND) == "workflow"
    mock_llmobs_span_writer.enqueue.assert_called_with(_expected_llmobs_non_llm_span_event(span, "workflow"))


def test_workflow_span_agentless(AgentlessLLMObs, mock_llmobs_span_agentless_writer):
    with AgentlessLLMObs.workflow(name="test_workflow") as span:
        assert span.name == "test_workflow"
        assert span.resource == "workflow"
        assert span.span_type == "llm"
        assert span.get_tag(SPAN_KIND) == "workflow"
    mock_llmobs_span_agentless_writer.enqueue.assert_called_with(_expected_llmobs_non_llm_span_event(span, "workflow"))


def test_agent_span(LLMObs, mock_llmobs_span_writer):
    with LLMObs.agent(name="test_agent") as span:
        assert span.name == "test_agent"
        assert span.resource == "agent"
        assert span.span_type == "llm"
        assert span.get_tag(SPAN_KIND) == "agent"
    mock_llmobs_span_writer.enqueue.assert_called_with(_expected_llmobs_llm_span_event(span, "agent"))


def test_agent_span_agentless(AgentlessLLMObs, mock_llmobs_span_agentless_writer):
    with AgentlessLLMObs.agent(name="test_agent") as span:
        assert span.name == "test_agent"
        assert span.resource == "agent"
        assert span.span_type == "llm"
        assert span.get_tag(SPAN_KIND) == "agent"
    mock_llmobs_span_agentless_writer.enqueue.assert_called_with(_expected_llmobs_llm_span_event(span, "agent"))


def test_embedding_span_no_model_raises_error(LLMObs):
    with pytest.raises(TypeError):
        with LLMObs.embedding(name="test_embedding", model_provider="test_provider"):
            pass


def test_embedding_span_empty_model_name_logs_warning(LLMObs, mock_logs):
    _ = LLMObs.embedding(model_name="", name="test_embedding", model_provider="test_provider")
    mock_logs.warning.assert_called_once_with("LLMObs.embedding() missing model_name")


def test_embedding_default_model_provider_set_to_custom(LLMObs):
    with LLMObs.embedding(model_name="test_model", name="test_embedding") as span:
        assert span.name == "test_embedding"
        assert span.resource == "embedding"
        assert span.span_type == "llm"
        assert span.get_tag(SPAN_KIND) == "embedding"
        assert span.get_tag(MODEL_NAME) == "test_model"
        assert span.get_tag(MODEL_PROVIDER) == "custom"


def test_embedding_span(LLMObs, mock_llmobs_span_writer):
    with LLMObs.embedding(model_name="test_model", name="test_embedding", model_provider="test_provider") as span:
        assert span.name == "test_embedding"
        assert span.resource == "embedding"
        assert span.span_type == "llm"
        assert span.get_tag(SPAN_KIND) == "embedding"
        assert span.get_tag(MODEL_NAME) == "test_model"
        assert span.get_tag(MODEL_PROVIDER) == "test_provider"

    mock_llmobs_span_writer.enqueue.assert_called_with(
        _expected_llmobs_llm_span_event(span, "embedding", model_name="test_model", model_provider="test_provider")
    )


def test_embedding_span_agentless(AgentlessLLMObs, mock_llmobs_span_agentless_writer):
    with AgentlessLLMObs.embedding(
        model_name="test_model", name="test_embedding", model_provider="test_provider"
    ) as span:
        assert span.name == "test_embedding"
        assert span.resource == "embedding"
        assert span.span_type == "llm"
        assert span.get_tag(SPAN_KIND) == "embedding"
        assert span.get_tag(MODEL_NAME) == "test_model"
        assert span.get_tag(MODEL_PROVIDER) == "test_provider"

    mock_llmobs_span_agentless_writer.enqueue.assert_called_with(
        _expected_llmobs_llm_span_event(span, "embedding", model_name="test_model", model_provider="test_provider")
    )


def test_annotate_no_active_span_logs_warning(LLMObs, mock_logs):
    LLMObs.annotate(parameters={"test": "test"})
    mock_logs.warning.assert_called_once_with("No span provided and no active LLMObs-generated span found.")


def test_annotate_non_llm_span_logs_warning(LLMObs, mock_logs):
    dummy_tracer = DummyTracer()
    with dummy_tracer.trace("root") as non_llmobs_span:
        LLMObs.annotate(span=non_llmobs_span, parameters={"test": "test"})
        mock_logs.warning.assert_called_once_with("Span must be an LLMObs-generated span.")


def test_annotate_finished_span_does_nothing(LLMObs, mock_logs):
    with LLMObs.llm(model_name="test_model", name="test_llm_call", model_provider="test_provider") as span:
        pass
    LLMObs.annotate(span=span, parameters={"test": "test"})
    mock_logs.warning.assert_called_once_with("Cannot annotate a finished span.")


def test_annotate_parameters(LLMObs, mock_logs):
    with LLMObs.llm(model_name="test_model", name="test_llm_call", model_provider="test_provider") as span:
        LLMObs.annotate(span=span, parameters={"temperature": 0.9, "max_tokens": 50})
        assert json.loads(span.get_tag(INPUT_PARAMETERS)) == {"temperature": 0.9, "max_tokens": 50}
        mock_logs.warning.assert_called_once_with(
            "Setting parameters is deprecated, please set parameters and other metadata as tags instead."
        )


def test_annotate_metadata(LLMObs):
    with LLMObs.llm(model_name="test_model", name="test_llm_call", model_provider="test_provider") as span:
        LLMObs.annotate(span=span, metadata={"temperature": 0.5, "max_tokens": 20, "top_k": 10, "n": 3})
        assert json.loads(span.get_tag(METADATA)) == {"temperature": 0.5, "max_tokens": 20, "top_k": 10, "n": 3}


def test_annotate_metadata_wrong_type_raises_warning(LLMObs, mock_logs):
    with LLMObs.llm(model_name="test_model", name="test_llm_call", model_provider="test_provider") as span:
        LLMObs.annotate(span=span, metadata="wrong_metadata")
        assert span.get_tag(METADATA) is None
        mock_logs.warning.assert_called_once_with("metadata must be a dictionary of string key-value pairs.")
        mock_logs.reset_mock()


def test_annotate_metadata_non_serializable_marks_with_placeholder_value(LLMObs):
    with LLMObs.llm(model_name="test_model", name="test_llm_call", model_provider="test_provider") as span:
        with mock.patch("ddtrace.llmobs._utils.log") as mock_logs:
            LLMObs.annotate(span=span, metadata={"unserializable": object()})
            metadata = json.loads(span.get_tag(METADATA))
            assert metadata is not None
            assert "[Unserializable object: <object object" in metadata["unserializable"]
            mock_logs.warning.assert_called_once_with(
                "I/O object is not JSON serializable. Defaulting to placeholder value instead."
            )


def test_annotate_tag(LLMObs):
    with LLMObs.llm(model_name="test_model", name="test_llm_call", model_provider="test_provider") as span:
        LLMObs.annotate(span=span, tags={"test_tag_name": "test_tag_value", "test_numeric_tag": 10})
        assert json.loads(span.get_tag(TAGS)) == {"test_tag_name": "test_tag_value", "test_numeric_tag": 10}


def test_annotate_tag_wrong_type(LLMObs, mock_logs):
    with LLMObs.llm(model_name="test_model", name="test_llm_call", model_provider="test_provider") as span:
        LLMObs.annotate(span=span, tags=12345)
        assert span.get_tag(TAGS) is None
        mock_logs.warning.assert_called_once_with(
            "span_tags must be a dictionary of string key - primitive value pairs."
        )


def test_annotate_tag_non_serializable_marks_with_placeholder_value(LLMObs):
    with LLMObs.llm(model_name="test_model", name="test_llm_call", model_provider="test_provider") as span:
        with mock.patch("ddtrace.llmobs._utils.log") as mock_logs:
            LLMObs.annotate(span=span, tags={"unserializable": object()})
            tags = json.loads(span.get_tag(TAGS))
            assert tags is not None
            assert "[Unserializable object:" in tags["unserializable"]
            mock_logs.warning.assert_called_once_with(
                "I/O object is not JSON serializable. Defaulting to placeholder value instead."
            )


def test_annotate_input_string(LLMObs):
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
        assert agent_span.get_tag(INPUT_VALUE) == "test_input"
    with LLMObs.retrieval() as retrieval_span:
        LLMObs.annotate(span=retrieval_span, input_data="test_input")
        assert retrieval_span.get_tag(INPUT_VALUE) == "test_input"


def test_annotate_input_serializable_value(LLMObs):
    with LLMObs.task() as task_span:
        LLMObs.annotate(span=task_span, input_data=["test_input"])
        assert task_span.get_tag(INPUT_VALUE) == '["test_input"]'
    with LLMObs.tool() as tool_span:
        LLMObs.annotate(span=tool_span, input_data={"test_input": "hello world"})
        assert tool_span.get_tag(INPUT_VALUE) == '{"test_input": "hello world"}'
    with LLMObs.workflow() as workflow_span:
        LLMObs.annotate(span=workflow_span, input_data=("asd", 123))
        assert workflow_span.get_tag(INPUT_VALUE) == '["asd", 123]'
    with LLMObs.agent() as agent_span:
        LLMObs.annotate(span=agent_span, input_data="test_input")
        assert agent_span.get_tag(INPUT_VALUE) == "test_input"
    with LLMObs.retrieval() as retrieval_span:
        LLMObs.annotate(span=retrieval_span, input_data=[0, 1, 2, 3, 4])
        assert retrieval_span.get_tag(INPUT_VALUE) == "[0, 1, 2, 3, 4]"


def test_annotate_input_value_non_serializable_marks_with_placeholder_value(LLMObs):
    with LLMObs.workflow() as span:
        with mock.patch("ddtrace.llmobs._utils.log") as mock_logs:
            LLMObs.annotate(span=span, input_data=object())
            input_value = span.get_tag(INPUT_VALUE)
            assert input_value is not None
            assert "[Unserializable object:" in input_value
            mock_logs.warning.assert_called_once_with(
                "I/O object is not JSON serializable. Defaulting to placeholder value instead."
            )


def test_annotate_input_llm_message(LLMObs):
    with LLMObs.llm(model_name="test_model") as span:
        LLMObs.annotate(span=span, input_data=[{"content": "test_input", "role": "human"}])
        assert json.loads(span.get_tag(INPUT_MESSAGES)) == [{"content": "test_input", "role": "human"}]


def test_annotate_input_llm_message_wrong_type(LLMObs, mock_logs):
    with LLMObs.llm(model_name="test_model") as span:
        LLMObs.annotate(span=span, input_data=[{"content": object()}])
        assert span.get_tag(INPUT_MESSAGES) is None
        mock_logs.warning.assert_called_once_with("Failed to parse input messages.", exc_info=True)


def test_llmobs_annotate_incorrect_message_content_type_raises_warning(LLMObs, mock_logs):
    with LLMObs.llm(model_name="test_model") as span:
        LLMObs.annotate(span=span, input_data={"role": "user", "content": {"nested": "yes"}})
        mock_logs.warning.assert_called_once_with("Failed to parse input messages.", exc_info=True)
        mock_logs.reset_mock()
        LLMObs.annotate(span=span, output_data={"role": "user", "content": {"nested": "yes"}})
        mock_logs.warning.assert_called_once_with("Failed to parse output messages.", exc_info=True)


def test_annotate_document_str(LLMObs):
    with LLMObs.embedding(model_name="test_model") as span:
        LLMObs.annotate(span=span, input_data="test_document_text")
        documents = json.loads(span.get_tag(INPUT_DOCUMENTS))
        assert documents
        assert len(documents) == 1
        assert documents[0]["text"] == "test_document_text"
    with LLMObs.retrieval() as span:
        LLMObs.annotate(span=span, output_data="test_document_text")
        documents = json.loads(span.get_tag(OUTPUT_DOCUMENTS))
        assert documents
        assert len(documents) == 1
        assert documents[0]["text"] == "test_document_text"


def test_annotate_document_dict(LLMObs):
    with LLMObs.embedding(model_name="test_model") as span:
        LLMObs.annotate(span=span, input_data={"text": "test_document_text"})
        documents = json.loads(span.get_tag(INPUT_DOCUMENTS))
        assert documents
        assert len(documents) == 1
        assert documents[0]["text"] == "test_document_text"
    with LLMObs.retrieval() as span:
        LLMObs.annotate(span=span, output_data={"text": "test_document_text"})
        documents = json.loads(span.get_tag(OUTPUT_DOCUMENTS))
        assert documents
        assert len(documents) == 1
        assert documents[0]["text"] == "test_document_text"


def test_annotate_document_list(LLMObs):
    with LLMObs.embedding(model_name="test_model") as span:
        LLMObs.annotate(
            span=span,
            input_data=[{"text": "test_document_text"}, {"text": "text", "name": "name", "score": 0.9, "id": "id"}],
        )
        documents = json.loads(span.get_tag(INPUT_DOCUMENTS))
        assert documents
        assert len(documents) == 2
        assert documents[0]["text"] == "test_document_text"
        assert documents[1]["text"] == "text"
        assert documents[1]["name"] == "name"
        assert documents[1]["id"] == "id"
        assert documents[1]["score"] == 0.9
    with LLMObs.retrieval() as span:
        LLMObs.annotate(
            span=span,
            output_data=[{"text": "test_document_text"}, {"text": "text", "name": "name", "score": 0.9, "id": "id"}],
        )
        documents = json.loads(span.get_tag(OUTPUT_DOCUMENTS))
        assert documents
        assert len(documents) == 2
        assert documents[0]["text"] == "test_document_text"
        assert documents[1]["text"] == "text"
        assert documents[1]["name"] == "name"
        assert documents[1]["id"] == "id"
        assert documents[1]["score"] == 0.9


def test_annotate_incorrect_document_type_raises_warning(LLMObs, mock_logs):
    with LLMObs.embedding(model_name="test_model") as span:
        LLMObs.annotate(span=span, input_data={"text": 123})
        mock_logs.warning.assert_called_once_with("Failed to parse input documents.", exc_info=True)
        mock_logs.reset_mock()
        LLMObs.annotate(span=span, input_data=123)
        mock_logs.warning.assert_called_once_with("Failed to parse input documents.", exc_info=True)
        mock_logs.reset_mock()
        LLMObs.annotate(span=span, input_data=object())
        mock_logs.warning.assert_called_once_with("Failed to parse input documents.", exc_info=True)
        mock_logs.reset_mock()
    with LLMObs.retrieval() as span:
        LLMObs.annotate(span=span, output_data=[{"score": 0.9, "id": "id", "name": "name"}])
        mock_logs.warning.assert_called_once_with("Failed to parse output documents.", exc_info=True)
        mock_logs.reset_mock()
        LLMObs.annotate(span=span, output_data=123)
        mock_logs.warning.assert_called_once_with("Failed to parse output documents.", exc_info=True)
        mock_logs.reset_mock()
        LLMObs.annotate(span=span, output_data=object())
        mock_logs.warning.assert_called_once_with("Failed to parse output documents.", exc_info=True)


def test_annotate_output_embedding_non_serializable_marks_with_placeholder_value(LLMObs):
    with LLMObs.embedding(model_name="test_model") as span:
        with mock.patch("ddtrace.llmobs._utils.log") as mock_logs:
            LLMObs.annotate(span=span, output_data=object())
            output_value = json.loads(span.get_tag(OUTPUT_VALUE))
            assert output_value is not None
            assert "[Unserializable object:" in output_value
            mock_logs.warning.assert_called_once_with(
                "I/O object is not JSON serializable. Defaulting to placeholder value instead."
            )


def test_annotate_input_retrieval_non_serializable_marks_with_placeholder_value(LLMObs):
    with LLMObs.retrieval() as span:
        with mock.patch("ddtrace.llmobs._utils.log") as mock_logs:
            LLMObs.annotate(span=span, input_data=object())
            input_value = json.loads(span.get_tag(INPUT_VALUE))
            assert input_value is not None
            assert "[Unserializable object:" in input_value
            mock_logs.warning.assert_called_once_with(
                "I/O object is not JSON serializable. Defaulting to placeholder value instead."
            )


def test_annotate_document_no_text_raises_warning(LLMObs, mock_logs):
    with LLMObs.embedding(model_name="test_model") as span:
        LLMObs.annotate(span=span, input_data=[{"score": 0.9, "id": "id", "name": "name"}])
        mock_logs.warning.assert_called_once_with("Failed to parse input documents.", exc_info=True)
    mock_logs.reset_mock()
    with LLMObs.retrieval() as span:
        LLMObs.annotate(span=span, output_data=[{"score": 0.9, "id": "id", "name": "name"}])
        mock_logs.warning.assert_called_once_with("Failed to parse output documents.", exc_info=True)


def test_annotate_incorrect_document_field_type_raises_warning(LLMObs, mock_logs):
    with LLMObs.embedding(model_name="test_model") as span:
        LLMObs.annotate(span=span, input_data=[{"text": "test_document_text", "score": "0.9"}])
        mock_logs.warning.assert_called_once_with("Failed to parse input documents.", exc_info=True)
    mock_logs.reset_mock()
    with LLMObs.embedding(model_name="test_model") as span:
        LLMObs.annotate(
            span=span, input_data=[{"text": "text", "id": 123, "score": "0.9", "name": ["h", "e", "l", "l", "o"]}]
        )
        mock_logs.warning.assert_called_once_with("Failed to parse input documents.", exc_info=True)
    mock_logs.reset_mock()
    with LLMObs.retrieval() as span:
        LLMObs.annotate(span=span, output_data=[{"text": "test_document_text", "score": "0.9"}])
        mock_logs.warning.assert_called_once_with("Failed to parse output documents.", exc_info=True)
    mock_logs.reset_mock()
    with LLMObs.retrieval() as span:
        LLMObs.annotate(
            span=span, output_data=[{"text": "text", "id": 123, "score": "0.9", "name": ["h", "e", "l", "l", "o"]}]
        )
        mock_logs.warning.assert_called_once_with("Failed to parse output documents.", exc_info=True)


def test_annotate_output_string(LLMObs):
    with LLMObs.llm(model_name="test_model") as llm_span:
        LLMObs.annotate(span=llm_span, output_data="test_output")
        assert json.loads(llm_span.get_tag(OUTPUT_MESSAGES)) == [{"content": "test_output"}]
    with LLMObs.embedding(model_name="test_model") as embedding_span:
        LLMObs.annotate(span=embedding_span, output_data="test_output")
        assert embedding_span.get_tag(OUTPUT_VALUE) == "test_output"
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
        assert agent_span.get_tag(OUTPUT_VALUE) == "test_output"


def test_annotate_output_serializable_value(LLMObs):
    with LLMObs.embedding(model_name="test_model") as embedding_span:
        LLMObs.annotate(span=embedding_span, output_data=[[0, 1, 2, 3], [4, 5, 6, 7]])
        assert embedding_span.get_tag(OUTPUT_VALUE) == "[[0, 1, 2, 3], [4, 5, 6, 7]]"
    with LLMObs.task() as task_span:
        LLMObs.annotate(span=task_span, output_data=["test_output"])
        assert task_span.get_tag(OUTPUT_VALUE) == '["test_output"]'
    with LLMObs.tool() as tool_span:
        LLMObs.annotate(span=tool_span, output_data={"test_output": "hello world"})
        assert tool_span.get_tag(OUTPUT_VALUE) == '{"test_output": "hello world"}'
    with LLMObs.workflow() as workflow_span:
        LLMObs.annotate(span=workflow_span, output_data=("asd", 123))
        assert workflow_span.get_tag(OUTPUT_VALUE) == '["asd", 123]'
    with LLMObs.agent() as agent_span:
        LLMObs.annotate(span=agent_span, output_data="test_output")
        assert agent_span.get_tag(OUTPUT_VALUE) == "test_output"


def test_annotate_output_value_non_serializable_marks_with_placeholder_value(LLMObs):
    with LLMObs.workflow() as span:
        with mock.patch("ddtrace.llmobs._utils.log") as mock_logs:
            LLMObs.annotate(span=span, output_data=object())
            output_value = json.loads(span.get_tag(OUTPUT_VALUE))
            assert output_value is not None
            assert "[Unserializable object:" in output_value
            mock_logs.warning.assert_called_once_with(
                "I/O object is not JSON serializable. Defaulting to placeholder value instead."
            )


def test_annotate_output_llm_message(LLMObs):
    with LLMObs.llm(model_name="test_model") as llm_span:
        LLMObs.annotate(span=llm_span, output_data=[{"content": "test_output", "role": "human"}])
        assert json.loads(llm_span.get_tag(OUTPUT_MESSAGES)) == [{"content": "test_output", "role": "human"}]


def test_annotate_output_llm_message_wrong_type(LLMObs, mock_logs):
    with LLMObs.llm(model_name="test_model") as llm_span:
        LLMObs.annotate(span=llm_span, output_data=[{"content": object()}])
        assert llm_span.get_tag(OUTPUT_MESSAGES) is None
        mock_logs.warning.assert_called_once_with("Failed to parse output messages.", exc_info=True)


def test_annotate_metrics(LLMObs):
    with LLMObs.llm(model_name="test_model") as span:
        LLMObs.annotate(span=span, metrics={"input_tokens": 10, "output_tokens": 20, "total_tokens": 30})
        assert json.loads(span.get_tag(METRICS)) == {"input_tokens": 10, "output_tokens": 20, "total_tokens": 30}


def test_annotate_metrics_wrong_type(LLMObs, mock_logs):
    with LLMObs.llm(model_name="test_model") as llm_span:
        LLMObs.annotate(span=llm_span, metrics=12345)
        assert llm_span.get_tag(METRICS) is None
        mock_logs.warning.assert_called_once_with("metrics must be a dictionary of string key - numeric value pairs.")
        mock_logs.reset_mock()


def test_annotate_metrics_unserializable_uses_placeholder(LLMObs, mock_logs):
    with LLMObs.llm(model_name="test_model") as llm_span:
        LLMObs.annotate(span=llm_span, metrics={"content": object()})
        metrics = json.loads(llm_span.get_tag(METRICS))
        assert metrics is not None
        assert "[Unserializable object: <object object at" in metrics["content"]


def test_span_error_sets_error(LLMObs, mock_llmobs_span_writer):
    with pytest.raises(ValueError):
        with LLMObs.llm(model_name="test_model", model_provider="test_model_provider") as span:
            raise ValueError("test error message")
    mock_llmobs_span_writer.enqueue.assert_called_with(
        _expected_llmobs_llm_span_event(
            span,
            model_name="test_model",
            model_provider="test_model_provider",
            error="builtins.ValueError",
            error_message="test error message",
            error_stack=span.get_tag("error.stack"),
        )
    )


def test_span_error_sets_error_agentless(AgentlessLLMObs, mock_llmobs_span_agentless_writer):
    with pytest.raises(ValueError):
        with AgentlessLLMObs.llm(model_name="test_model", model_provider="test_model_provider") as span:
            raise ValueError("test error message")
    mock_llmobs_span_agentless_writer.enqueue.assert_called_with(
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
def test_tags(ddtrace_global_config, LLMObs, mock_llmobs_span_writer, monkeypatch):
    with LLMObs.task(name="test_task") as span:
        pass
    mock_llmobs_span_writer.enqueue.assert_called_with(
        _expected_llmobs_non_llm_span_event(
            span,
            "task",
            tags={"version": "1.2.3", "env": "test_env", "service": "test_service", "ml_app": "test_app_name"},
        )
    )


@pytest.mark.parametrize(
    "ddtrace_global_config",
    [dict(version="1.2.3", env="test_env", service="test_service", _llmobs_ml_app="test_app_name")],
)
def test_tags_agentless(ddtrace_global_config, AgentlessLLMObs, mock_llmobs_span_agentless_writer, monkeypatch):
    with AgentlessLLMObs.task(name="test_task") as span:
        pass
    mock_llmobs_span_agentless_writer.enqueue.assert_called_with(
        _expected_llmobs_non_llm_span_event(
            span,
            "task",
            tags={"version": "1.2.3", "env": "test_env", "service": "test_service", "ml_app": "test_app_name"},
        )
    )


def test_ml_app_override(LLMObs, mock_llmobs_span_writer):
    with LLMObs.task(name="test_task", ml_app="test_app") as span:
        pass
    mock_llmobs_span_writer.enqueue.assert_called_with(
        _expected_llmobs_non_llm_span_event(span, "task", tags={"ml_app": "test_app"})
    )
    with LLMObs.tool(name="test_tool", ml_app="test_app") as span:
        pass
    mock_llmobs_span_writer.enqueue.assert_called_with(
        _expected_llmobs_non_llm_span_event(span, "tool", tags={"ml_app": "test_app"})
    )
    with LLMObs.llm(model_name="model_name", name="test_llm", ml_app="test_app") as span:
        pass
    mock_llmobs_span_writer.enqueue.assert_called_with(
        _expected_llmobs_llm_span_event(
            span, "llm", model_name="model_name", model_provider="custom", tags={"ml_app": "test_app"}
        )
    )
    with LLMObs.embedding(model_name="model_name", name="test_embedding", ml_app="test_app") as span:
        pass
    mock_llmobs_span_writer.enqueue.assert_called_with(
        _expected_llmobs_llm_span_event(
            span, "embedding", model_name="model_name", model_provider="custom", tags={"ml_app": "test_app"}
        )
    )
    with LLMObs.workflow(name="test_workflow", ml_app="test_app") as span:
        pass
    mock_llmobs_span_writer.enqueue.assert_called_with(
        _expected_llmobs_non_llm_span_event(span, "workflow", tags={"ml_app": "test_app"})
    )
    with LLMObs.agent(name="test_agent", ml_app="test_app") as span:
        pass
    mock_llmobs_span_writer.enqueue.assert_called_with(
        _expected_llmobs_llm_span_event(span, "agent", tags={"ml_app": "test_app"})
    )
    with LLMObs.retrieval(name="test_retrieval", ml_app="test_app") as span:
        pass
    mock_llmobs_span_writer.enqueue.assert_called_with(
        _expected_llmobs_non_llm_span_event(span, "retrieval", tags={"ml_app": "test_app"})
    )


def test_ml_app_override_agentless(AgentlessLLMObs, mock_llmobs_span_agentless_writer):
    with AgentlessLLMObs.task(name="test_task", ml_app="test_app") as span:
        pass
    mock_llmobs_span_agentless_writer.enqueue.assert_called_with(
        _expected_llmobs_non_llm_span_event(span, "task", tags={"ml_app": "test_app"})
    )
    with AgentlessLLMObs.tool(name="test_tool", ml_app="test_app") as span:
        pass
    mock_llmobs_span_agentless_writer.enqueue.assert_called_with(
        _expected_llmobs_non_llm_span_event(span, "tool", tags={"ml_app": "test_app"})
    )
    with AgentlessLLMObs.llm(model_name="model_name", name="test_llm", ml_app="test_app") as span:
        pass
    mock_llmobs_span_agentless_writer.enqueue.assert_called_with(
        _expected_llmobs_llm_span_event(
            span, "llm", model_name="model_name", model_provider="custom", tags={"ml_app": "test_app"}
        )
    )
    with AgentlessLLMObs.embedding(model_name="model_name", name="test_embedding", ml_app="test_app") as span:
        pass
    mock_llmobs_span_agentless_writer.enqueue.assert_called_with(
        _expected_llmobs_llm_span_event(
            span, "embedding", model_name="model_name", model_provider="custom", tags={"ml_app": "test_app"}
        )
    )
    with AgentlessLLMObs.workflow(name="test_workflow", ml_app="test_app") as span:
        pass
    mock_llmobs_span_agentless_writer.enqueue.assert_called_with(
        _expected_llmobs_non_llm_span_event(span, "workflow", tags={"ml_app": "test_app"})
    )
    with AgentlessLLMObs.agent(name="test_agent", ml_app="test_app") as span:
        pass
    mock_llmobs_span_agentless_writer.enqueue.assert_called_with(
        _expected_llmobs_llm_span_event(span, "agent", tags={"ml_app": "test_app"})
    )
    with AgentlessLLMObs.retrieval(name="test_retrieval", ml_app="test_app") as span:
        pass
    mock_llmobs_span_agentless_writer.enqueue.assert_called_with(
        _expected_llmobs_non_llm_span_event(span, "retrieval", tags={"ml_app": "test_app"})
    )


def test_export_span_specified_span_is_incorrect_type_raises_warning(LLMObs, mock_logs):
    LLMObs.export_span(span="asd")
    mock_logs.warning.assert_called_once_with("Failed to export span. Span must be a valid Span object.")


def test_export_span_specified_span_is_not_llmobs_span_raises_warning(LLMObs, mock_logs):
    with DummyTracer().trace("non_llmobs_span") as span:
        LLMObs.export_span(span=span)
    mock_logs.warning.assert_called_once_with("Span must be an LLMObs-generated span.")


def test_export_span_specified_span_returns_span_context(LLMObs):
    with LLMObs.llm(model_name="test_model", name="test_llm_call", model_provider="test_provider") as span:
        span_context = LLMObs.export_span(span=span)
        assert span_context is not None
        assert span_context["span_id"] == str(span.span_id)
        assert span_context["trace_id"] == "{:x}".format(span.trace_id)


def test_export_span_no_specified_span_no_active_span_raises_warning(LLMObs, mock_logs):
    LLMObs.export_span()
    mock_logs.warning.assert_called_once_with("No span provided and no active LLMObs-generated span found.")


def test_export_span_active_span_not_llmobs_span_raises_warning(LLMObs, mock_logs):
    with LLMObs._instance.tracer.trace("non_llmobs_span"):
        LLMObs.export_span()
    mock_logs.warning.assert_called_once_with("Span must be an LLMObs-generated span.")


def test_export_span_no_specified_span_returns_exported_active_span(LLMObs):
    with LLMObs.llm(model_name="test_model", name="test_llm_call", model_provider="test_provider") as span:
        span_context = LLMObs.export_span()
        assert span_context is not None
        assert span_context["span_id"] == str(span.span_id)
        assert span_context["trace_id"] == "{:x}".format(span.trace_id)


def test_submit_evaluation_llmobs_disabled_raises_warning(LLMObs, mock_logs):
    LLMObs.disable()
    LLMObs.submit_evaluation(
        span_context={"span_id": "123", "trace_id": "456"}, label="toxicity", metric_type="categorical", value="high"
    )
    mock_logs.warning.assert_called_once_with(
        "LLMObs.submit_evaluation() called when LLMObs is not enabled. Evaluation metric data will not be sent."
    )


def test_submit_evaluation_no_api_key_raises_warning(AgentlessLLMObs, mock_logs):
    with override_global_config(dict(_dd_api_key="")):
        AgentlessLLMObs.submit_evaluation(
            span_context={"span_id": "123", "trace_id": "456"},
            label="toxicity",
            metric_type="categorical",
            value="high",
        )
        mock_logs.warning.assert_called_once_with(
            "DD_API_KEY is required for sending evaluation metrics. Evaluation metric data will not be sent. "
            "Ensure this configuration is set before running your application."
        )


def test_submit_evaluation_ml_app_raises_warning(LLMObs, mock_logs):
    with override_global_config(dict(_llmobs_ml_app="")):
        LLMObs.submit_evaluation(
            span_context={"span_id": "123", "trace_id": "456"},
            label="toxicity",
            metric_type="categorical",
            value="high",
        )
        mock_logs.warning.assert_called_once_with(
            "ML App name is required for sending evaluation metrics. Evaluation metric data will not be sent. "
            "Ensure this configuration is set before running your application."
        )


def test_submit_evaluation_span_context_incorrect_type_raises_warning(LLMObs, mock_logs):
    LLMObs.submit_evaluation(span_context="asd", label="toxicity", metric_type="categorical", value="high")
    mock_logs.warning.assert_called_once_with(
        "span_context must be a dictionary containing both span_id and trace_id keys. "
        "LLMObs.export_span() can be used to generate this dictionary from a given span."
    )


def test_submit_evaluation_empty_span_or_trace_id_raises_warning(LLMObs, mock_logs):
    LLMObs.submit_evaluation(
        span_context={"trace_id": "456"}, label="toxicity", metric_type="categorical", value="high"
    )
    mock_logs.warning.assert_called_once_with(
        "span_id and trace_id must both be specified for the given evaluation metric to be submitted."
    )
    mock_logs.reset_mock()
    LLMObs.submit_evaluation(span_context={"span_id": "456"}, label="toxicity", metric_type="categorical", value="high")
    mock_logs.warning.assert_called_once_with(
        "span_id and trace_id must both be specified for the given evaluation metric to be submitted."
    )


def test_submit_evaluation_invalid_timestamp_raises_warning(LLMObs, mock_logs):
    LLMObs.submit_evaluation(
        span_context={"span_id": "123", "trace_id": "456"},
        label="",
        metric_type="categorical",
        value="high",
        ml_app="dummy",
        timestamp_ms="invalid",
    )
    mock_logs.warning.assert_called_once_with(
        "timestamp_ms must be a non-negative integer. Evaluation metric data will not be sent"
    )


def test_submit_evaluation_empty_label_raises_warning(LLMObs, mock_logs):
    LLMObs.submit_evaluation(
        span_context={"span_id": "123", "trace_id": "456"}, label="", metric_type="categorical", value="high"
    )
    mock_logs.warning.assert_called_once_with("label must be the specified name of the evaluation metric.")


def test_submit_evaluation_incorrect_metric_type_raises_warning(LLMObs, mock_logs):
    LLMObs.submit_evaluation(
        span_context={"span_id": "123", "trace_id": "456"}, label="toxicity", metric_type="wrong", value="high"
    )
    mock_logs.warning.assert_called_once_with("metric_type must be one of 'categorical' or 'score'.")
    mock_logs.reset_mock()
    LLMObs.submit_evaluation(
        span_context={"span_id": "123", "trace_id": "456"}, label="toxicity", metric_type="", value="high"
    )
    mock_logs.warning.assert_called_once_with("metric_type must be one of 'categorical' or 'score'.")


def test_submit_evaluation_numerical_value_raises_unsupported_warning(LLMObs, mock_logs):
    LLMObs.submit_evaluation(
        span_context={"span_id": "123", "trace_id": "456"}, label="token_count", metric_type="numerical", value="high"
    )
    mock_logs.warning.assert_has_calls(
        [
            mock.call(
                "The evaluation metric type 'numerical' is unsupported. Use 'score' instead. "
                "Converting `numerical` metric to `score` type."
            ),
        ]
    )


def test_submit_evaluation_incorrect_numerical_value_type_raises_warning(LLMObs, mock_logs):
    LLMObs.submit_evaluation(
        span_context={"span_id": "123", "trace_id": "456"}, label="token_count", metric_type="numerical", value="high"
    )
    mock_logs.warning.assert_has_calls(
        [
            mock.call("value must be an integer or float for a score metric."),
        ]
    )


def test_submit_evaluation_incorrect_score_value_type_raises_warning(LLMObs, mock_logs):
    LLMObs.submit_evaluation(
        span_context={"span_id": "123", "trace_id": "456"}, label="token_count", metric_type="score", value="high"
    )
    mock_logs.warning.assert_called_once_with("value must be an integer or float for a score metric.")


def test_submit_evaluation_invalid_tags_raises_warning(LLMObs, mock_logs):
    LLMObs.submit_evaluation(
        span_context={"span_id": "123", "trace_id": "456"},
        label="toxicity",
        metric_type="categorical",
        value="high",
        tags=["invalid"],
    )
    mock_logs.warning.assert_called_once_with("tags must be a dictionary of string key-value pairs.")


@pytest.mark.parametrize(
    "ddtrace_global_config",
    [dict(_llmobs_ml_app="test_app_name")],
)
def test_submit_evaluation_non_string_tags_raises_warning_but_still_submits(
    LLMObs, mock_logs, mock_llmobs_eval_metric_writer
):
    LLMObs.submit_evaluation(
        span_context={"span_id": "123", "trace_id": "456"},
        label="toxicity",
        metric_type="categorical",
        value="high",
        tags={1: 2, "foo": "bar"},
        ml_app="dummy",
    )
    mock_logs.warning.assert_called_once_with("Failed to parse tags. Tags for evaluation metrics must be strings.")
    mock_logs.reset_mock()
    mock_llmobs_eval_metric_writer.enqueue.assert_called_with(
        _expected_llmobs_eval_metric_event(
            ml_app="dummy",
            span_id="123",
            trace_id="456",
            label="toxicity",
            metric_type="categorical",
            categorical_value="high",
            tags=["ddtrace.version:{}".format(ddtrace.__version__), "ml_app:dummy", "foo:bar"],
        )
    )


@pytest.mark.parametrize(
    "ddtrace_global_config",
    [dict(ddtrace="1.2.3", env="test_env", service="test_service", _llmobs_ml_app="test_app_name")],
)
def test_submit_evaluation_metric_tags(LLMObs, mock_llmobs_eval_metric_writer):
    LLMObs.submit_evaluation(
        span_context={"span_id": "123", "trace_id": "456"},
        label="toxicity",
        metric_type="categorical",
        value="high",
        tags={"foo": "bar", "bee": "baz", "ml_app": "ml_app_override"},
        ml_app="ml_app_override",
    )
    mock_llmobs_eval_metric_writer.enqueue.assert_called_with(
        _expected_llmobs_eval_metric_event(
            ml_app="ml_app_override",
            span_id="123",
            trace_id="456",
            label="toxicity",
            metric_type="categorical",
            categorical_value="high",
            tags=["ddtrace.version:{}".format(ddtrace.__version__), "ml_app:ml_app_override", "foo:bar", "bee:baz"],
        )
    )


def test_submit_evaluation_enqueues_writer_with_categorical_metric(LLMObs, mock_llmobs_eval_metric_writer):
    LLMObs.submit_evaluation(
        span_context={"span_id": "123", "trace_id": "456"},
        label="toxicity",
        metric_type="categorical",
        value="high",
        ml_app="dummy",
    )
    mock_llmobs_eval_metric_writer.enqueue.assert_called_with(
        _expected_llmobs_eval_metric_event(
            ml_app="dummy",
            span_id="123",
            trace_id="456",
            label="toxicity",
            metric_type="categorical",
            categorical_value="high",
        )
    )
    mock_llmobs_eval_metric_writer.reset_mock()
    with LLMObs.llm(model_name="test_model", name="test_llm_call", model_provider="test_provider") as span:
        LLMObs.submit_evaluation(
            span_context=LLMObs.export_span(span),
            label="toxicity",
            metric_type="categorical",
            value="high",
            ml_app="dummy",
        )
    mock_llmobs_eval_metric_writer.enqueue.assert_called_with(
        _expected_llmobs_eval_metric_event(
            ml_app="dummy",
            span_id=str(span.span_id),
            trace_id="{:x}".format(span.trace_id),
            label="toxicity",
            metric_type="categorical",
            categorical_value="high",
        )
    )


def test_submit_evaluation_enqueues_writer_with_score_metric(LLMObs, mock_llmobs_eval_metric_writer):
    LLMObs.submit_evaluation(
        span_context={"span_id": "123", "trace_id": "456"},
        label="sentiment",
        metric_type="score",
        value=0.9,
        ml_app="dummy",
    )
    mock_llmobs_eval_metric_writer.enqueue.assert_called_with(
        _expected_llmobs_eval_metric_event(
            span_id="123", trace_id="456", label="sentiment", metric_type="score", score_value=0.9, ml_app="dummy"
        )
    )
    mock_llmobs_eval_metric_writer.reset_mock()
    with LLMObs.llm(model_name="test_model", name="test_llm_call", model_provider="test_provider") as span:
        LLMObs.submit_evaluation(
            span_context=LLMObs.export_span(span), label="sentiment", metric_type="score", value=0.9, ml_app="dummy"
        )
    mock_llmobs_eval_metric_writer.enqueue.assert_called_with(
        _expected_llmobs_eval_metric_event(
            span_id=str(span.span_id),
            trace_id="{:x}".format(span.trace_id),
            label="sentiment",
            metric_type="score",
            score_value=0.9,
            ml_app="dummy",
        )
    )


def test_submit_evaluation_with_numerical_metric_enqueues_writer_with_score_metric(
    LLMObs, mock_llmobs_eval_metric_writer
):
    LLMObs.submit_evaluation(
        span_context={"span_id": "123", "trace_id": "456"},
        label="token_count",
        metric_type="numerical",
        value=35,
        ml_app="dummy",
    )
    mock_llmobs_eval_metric_writer.enqueue.assert_called_with(
        _expected_llmobs_eval_metric_event(
            ml_app="dummy", span_id="123", trace_id="456", label="token_count", metric_type="score", score_value=35
        )
    )
    mock_llmobs_eval_metric_writer.reset_mock()
    with LLMObs.llm(model_name="test_model", name="test_llm_call", model_provider="test_provider") as span:
        LLMObs.submit_evaluation(
            span_context=LLMObs.export_span(span),
            label="token_count",
            metric_type="numerical",
            value=35,
            ml_app="dummy",
        )
    mock_llmobs_eval_metric_writer.enqueue.assert_called_with(
        _expected_llmobs_eval_metric_event(
            ml_app="dummy",
            span_id=str(span.span_id),
            trace_id="{:x}".format(span.trace_id),
            label="token_count",
            metric_type="score",
            score_value=35,
        )
    )


def test_flush_calls_periodic_agentless(
    AgentlessLLMObs, mock_llmobs_span_agentless_writer, mock_llmobs_eval_metric_writer
):
    AgentlessLLMObs.flush()
    mock_llmobs_span_agentless_writer.periodic.assert_called_once()
    mock_llmobs_eval_metric_writer.periodic.assert_called_once()


def test_flush_does_not_call_period_when_llmobs_is_disabled(
    LLMObs, mock_llmobs_span_writer, mock_llmobs_eval_metric_writer, mock_logs
):
    LLMObs.disable()
    LLMObs.flush()
    mock_llmobs_span_writer.periodic.assert_not_called()
    mock_llmobs_eval_metric_writer.periodic.assert_not_called()
    mock_logs.warning.assert_has_calls(
        [mock.call("flushing when LLMObs is disabled. No spans or evaluation metrics will be sent.")]
    )
    LLMObs.enable()


def test_flush_does_not_call_period_when_llmobs_is_disabled_agentless(
    AgentlessLLMObs, mock_llmobs_span_agentless_writer, mock_llmobs_eval_metric_writer, mock_logs
):
    AgentlessLLMObs.disable()
    AgentlessLLMObs.flush()
    mock_llmobs_span_agentless_writer.periodic.assert_not_called()
    mock_llmobs_eval_metric_writer.periodic.assert_not_called()
    mock_logs.warning.assert_has_calls(
        [mock.call("flushing when LLMObs is disabled. No spans or evaluation metrics will be sent.")]
    )
    AgentlessLLMObs.enable()


def test_inject_distributed_headers_llmobs_disabled_does_nothing(LLMObs, mock_logs):
    LLMObs.disable()
    headers = LLMObs.inject_distributed_headers({}, span=None)
    mock_logs.warning.assert_called_once_with(
        "LLMObs.inject_distributed_headers() called when LLMObs is not enabled. "
        "Distributed context will not be injected."
    )
    assert headers == {}


def test_inject_distributed_headers_not_dict_logs_warning(LLMObs, mock_logs):
    headers = LLMObs.inject_distributed_headers("not a dictionary", span=None)
    mock_logs.warning.assert_called_once_with("request_headers must be a dictionary of string key-value pairs.")
    assert headers == "not a dictionary"
    mock_logs.reset_mock()
    headers = LLMObs.inject_distributed_headers(123, span=None)
    mock_logs.warning.assert_called_once_with("request_headers must be a dictionary of string key-value pairs.")
    assert headers == 123
    mock_logs.reset_mock()
    headers = LLMObs.inject_distributed_headers(None, span=None)
    mock_logs.warning.assert_called_once_with("request_headers must be a dictionary of string key-value pairs.")
    assert headers is None


def test_inject_distributed_headers_no_active_span_logs_warning(LLMObs, mock_logs):
    headers = LLMObs.inject_distributed_headers({}, span=None)
    mock_logs.warning.assert_called_once_with("No span provided and no currently active span found.")
    assert headers == {}


def test_inject_distributed_headers_span_calls_httppropagator_inject(LLMObs, mock_logs):
    span = LLMObs._instance.tracer.trace("test_span")
    with mock.patch("ddtrace.propagation.http.HTTPPropagator.inject") as mock_inject:
        LLMObs.inject_distributed_headers({}, span=span)
        assert mock_inject.call_count == 1
        mock_inject.assert_called_once_with(span.context, {})


def test_inject_distributed_headers_current_active_span_injected(LLMObs, mock_logs):
    span = LLMObs._instance.tracer.trace("test_span")
    with mock.patch("ddtrace.llmobs._llmobs.HTTPPropagator.inject") as mock_inject:
        LLMObs.inject_distributed_headers({}, span=None)
        assert mock_inject.call_count == 1
        mock_inject.assert_called_once_with(span.context, {})


def test_activate_distributed_headers_llmobs_disabled_does_nothing(LLMObs, mock_logs):
    LLMObs.disable()
    LLMObs.activate_distributed_headers({})
    mock_logs.warning.assert_called_once_with(
        "LLMObs.activate_distributed_headers() called when LLMObs is not enabled. "
        "Distributed context will not be activated."
    )


def test_activate_distributed_headers_calls_httppropagator_extract(LLMObs, mock_logs):
    with mock.patch("ddtrace.llmobs._llmobs.HTTPPropagator.extract") as mock_extract:
        LLMObs.activate_distributed_headers({})
        assert mock_extract.call_count == 1
        mock_extract.assert_called_once_with({})


def test_activate_distributed_headers_no_trace_id_does_nothing(LLMObs, mock_logs):
    with mock.patch("ddtrace.llmobs._llmobs.HTTPPropagator.extract") as mock_extract:
        mock_extract.return_value = Context(span_id="123", meta={PROPAGATED_PARENT_ID_KEY: "123"})
        LLMObs.activate_distributed_headers({})
        assert mock_extract.call_count == 1
        mock_logs.warning.assert_called_once_with("Failed to extract trace ID or span ID from request headers.")


def test_activate_distributed_headers_no_span_id_does_nothing(LLMObs, mock_logs):
    with mock.patch("ddtrace.llmobs._llmobs.HTTPPropagator.extract") as mock_extract:
        mock_extract.return_value = Context(trace_id="123", meta={PROPAGATED_PARENT_ID_KEY: "123"})
        LLMObs.activate_distributed_headers({})
        assert mock_extract.call_count == 1
        mock_logs.warning.assert_called_once_with("Failed to extract trace ID or span ID from request headers.")


def test_activate_distributed_headers_no_llmobs_parent_id_does_nothing(LLMObs, mock_logs):
    with mock.patch("ddtrace.llmobs._llmobs.HTTPPropagator.extract") as mock_extract:
        dummy_context = Context(trace_id="123", span_id="456")
        mock_extract.return_value = dummy_context
        with mock.patch("ddtrace.llmobs.LLMObs._instance.tracer.context_provider.activate") as mock_activate:
            LLMObs.activate_distributed_headers({})
            assert mock_extract.call_count == 1
            mock_logs.warning.assert_called_once_with("Failed to extract LLMObs parent ID from request headers.")
            mock_activate.assert_called_once_with(dummy_context)


def test_activate_distributed_headers_activates_context(LLMObs, mock_logs):
    with mock.patch("ddtrace.llmobs._llmobs.HTTPPropagator.extract") as mock_extract:
        dummy_context = Context(trace_id="123", span_id="456", meta={PROPAGATED_PARENT_ID_KEY: "789"})
        mock_extract.return_value = dummy_context
        with mock.patch("ddtrace.llmobs.LLMObs._instance.tracer.context_provider.activate") as mock_activate:
            LLMObs.activate_distributed_headers({})
            assert mock_extract.call_count == 1
            mock_activate.assert_called_once_with(dummy_context)


def _task(llmobs_service, errors, original_pid, original_span_writer_id):
    """Task in test_llmobs_fork which asserts that LLMObs in a forked process correctly recreates the writer."""
    try:
        with llmobs_service.workflow():
            with llmobs_service.task():
                assert llmobs_service._instance.tracer._pid != original_pid
                assert id(llmobs_service._instance._llmobs_span_writer) != original_span_writer_id
        assert llmobs_service._instance._llmobs_span_writer.enqueue.call_count == 2
        assert llmobs_service._instance._llmobs_span_writer._encoder.encode.call_count == 2
    except AssertionError as e:
        errors.put(e)


def test_llmobs_fork_recreates_and_restarts_writer():
    """Test that forking a process correctly recreates and restarts the LLMObsSpanWriter."""
    with mock.patch("ddtrace.internal.writer.HTTPWriter._send_payload"):
        llmobs_service.enable(_tracer=DummyTracer(), ml_app="test_app")
        original_pid = llmobs_service._instance.tracer._pid
        original_span_writer = llmobs_service._instance._llmobs_span_writer
        pid = os.fork()
        if pid:  # parent
            assert llmobs_service._instance.tracer._pid == original_pid
            assert llmobs_service._instance._llmobs_span_writer == original_span_writer
            assert (
                llmobs_service._instance._trace_processor._span_writer == llmobs_service._instance._llmobs_span_writer
            )
            assert llmobs_service._instance._llmobs_span_writer.status == ServiceStatus.RUNNING
        else:  # child
            assert llmobs_service._instance.tracer._pid != original_pid
            assert llmobs_service._instance._llmobs_span_writer != original_span_writer
            assert (
                llmobs_service._instance._trace_processor._span_writer == llmobs_service._instance._llmobs_span_writer
            )
            assert llmobs_service._instance._llmobs_span_writer.status == ServiceStatus.RUNNING
            llmobs_service.disable()
            os._exit(12)

        _, status = os.waitpid(pid, 0)
        exit_code = os.WEXITSTATUS(status)
        assert exit_code == 12
        llmobs_service.disable()


def test_llmobs_fork_create_span(monkeypatch):
    """Test that forking a process correctly encodes new spans created in each process."""
    monkeypatch.setenv("_DD_LLMOBS_WRITER_INTERVAL", 5.0)
    with mock.patch("ddtrace.internal.writer.HTTPWriter._send_payload"):
        llmobs_service.enable(_tracer=DummyTracer(), ml_app="test_app")
        pid = os.fork()
        if pid:  # parent
            with llmobs_service.task():
                pass
            assert len(llmobs_service._instance._llmobs_span_writer._encoder) == 1
        else:  # child
            with llmobs_service.workflow():
                with llmobs_service.task():
                    pass
            assert len(llmobs_service._instance._llmobs_span_writer._encoder) == 2
            llmobs_service.disable()
            os._exit(12)

        _, status = os.waitpid(pid, 0)
        exit_code = os.WEXITSTATUS(status)
        assert exit_code == 12
        llmobs_service.disable()


def test_llmobs_fork_custom_filter(monkeypatch):
    """Test that forking a process correctly keeps any custom filters."""

    class CustomFilter(TraceFilter):
        def process_trace(self, trace):
            return trace

    monkeypatch.setenv("_DD_LLMOBS_WRITER_INTERVAL", 5.0)
    with mock.patch("ddtrace.internal.writer.HTTPWriter._send_payload"):
        tracer = DummyTracer()
        custom_filter = CustomFilter()
        tracer.configure(settings={"FILTERS": [custom_filter]})
        llmobs_service.enable(_tracer=tracer, ml_app="test_app")
        assert custom_filter in llmobs_service._instance.tracer._filters
        pid = os.fork()
        if pid:  # parent
            assert custom_filter in llmobs_service._instance.tracer._filters
            assert any(
                isinstance(tracer_filter, LLMObsTraceProcessor)
                for tracer_filter in llmobs_service._instance.tracer._filters
            )
        else:  # child
            assert custom_filter in llmobs_service._instance.tracer._filters
            assert any(
                isinstance(tracer_filter, LLMObsTraceProcessor)
                for tracer_filter in llmobs_service._instance.tracer._filters
            )
            llmobs_service.disable()
            os._exit(12)

        _, status = os.waitpid(pid, 0)
        exit_code = os.WEXITSTATUS(status)
        assert exit_code == 12
        llmobs_service.disable()
