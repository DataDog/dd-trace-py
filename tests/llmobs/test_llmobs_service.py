import os
import re
import threading
import time

import mock
import pytest

import ddtrace
from ddtrace.ext import SpanTypes
from ddtrace.internal.service import ServiceStatus
from ddtrace.internal.utils.formats import format_trace_id
from ddtrace.llmobs import LLMObs as llmobs_service
from ddtrace.llmobs._constants import INPUT_DOCUMENTS
from ddtrace.llmobs._constants import INPUT_MESSAGES
from ddtrace.llmobs._constants import INPUT_PROMPT
from ddtrace.llmobs._constants import INPUT_VALUE
from ddtrace.llmobs._constants import IS_EVALUATION_SPAN
from ddtrace.llmobs._constants import METADATA
from ddtrace.llmobs._constants import METRICS
from ddtrace.llmobs._constants import MODEL_NAME
from ddtrace.llmobs._constants import MODEL_PROVIDER
from ddtrace.llmobs._constants import OUTPUT_DOCUMENTS
from ddtrace.llmobs._constants import OUTPUT_MESSAGES
from ddtrace.llmobs._constants import OUTPUT_VALUE
from ddtrace.llmobs._constants import SESSION_ID
from ddtrace.llmobs._constants import SPAN_KIND
from ddtrace.llmobs._constants import SPAN_START_WHILE_DISABLED_WARNING
from ddtrace.llmobs._constants import TAGS
from ddtrace.llmobs._llmobs import SUPPORTED_LLMOBS_INTEGRATIONS
from ddtrace.llmobs._writer import LLMObsAgentlessEventClient
from ddtrace.llmobs._writer import LLMObsProxiedEventClient
from ddtrace.llmobs.utils import Prompt
from ddtrace.trace import Context
from tests.llmobs._utils import _expected_llmobs_eval_metric_event
from tests.llmobs._utils import _expected_llmobs_llm_span_event
from tests.llmobs._utils import _expected_llmobs_non_llm_span_event
from tests.utils import DummyTracer
from tests.utils import override_env
from tests.utils import override_global_config


RAGAS_AVAILABLE = os.getenv("RAGAS_AVAILABLE", False)


def run_llmobs_trace_filter(dummy_tracer):
    with dummy_tracer.trace("span1", span_type=SpanTypes.LLM) as span:
        span.set_tag_str(SPAN_KIND, "llm")
    return dummy_tracer._writer.pop()


def test_service_enable_proxy():
    with override_global_config(dict(_dd_api_key="<not-a-real-api-key>", _llmobs_ml_app="<ml-app-name>")):
        dummy_tracer = DummyTracer()
        llmobs_service.enable(_tracer=dummy_tracer, agentless_enabled=False)
        llmobs_instance = llmobs_service._instance
        assert llmobs_instance is not None
        assert llmobs_service.enabled
        assert llmobs_instance.tracer == dummy_tracer
        assert isinstance(llmobs_instance._llmobs_span_writer._clients[0], LLMObsProxiedEventClient)
        assert run_llmobs_trace_filter(dummy_tracer) is not None
        llmobs_service.disable()


def test_enable_agentless():
    with override_global_config(dict(_dd_api_key="<not-a-real-key>", _llmobs_ml_app="<ml-app-name>")):
        dummy_tracer = DummyTracer()
        llmobs_service.enable(_tracer=dummy_tracer, agentless_enabled=True)
        llmobs_instance = llmobs_service._instance
        assert llmobs_instance is not None
        assert llmobs_service.enabled
        assert llmobs_instance.tracer == dummy_tracer
        assert isinstance(llmobs_instance._llmobs_span_writer._clients[0], LLMObsAgentlessEventClient)
        assert run_llmobs_trace_filter(dummy_tracer) is not None

        llmobs_service.disable()


def test_enable_agent_proxy_when_agent_is_available(agent):
    with override_global_config(dict(_dd_api_key="<not-a-real-api-key>", _llmobs_ml_app="<ml-app-name>")):
        dummy_tracer = DummyTracer()
        llmobs_service.enable(_tracer=dummy_tracer)
        llmobs_instance = llmobs_service._instance
        assert llmobs_instance is not None
        assert llmobs_service.enabled
        assert isinstance(llmobs_instance._llmobs_span_writer._clients[0], LLMObsProxiedEventClient)

        llmobs_service.disable()


def test_enable_agentless_when_agent_is_not_available(no_agent):
    with override_global_config(dict(_dd_api_key="<not-a-real-api-key>", _llmobs_ml_app="<ml-app-name>")):
        dummy_tracer = DummyTracer()
        llmobs_service.enable(_tracer=dummy_tracer)
        llmobs_instance = llmobs_service._instance
        assert llmobs_instance is not None
        assert llmobs_service.enabled
        assert isinstance(llmobs_instance._llmobs_span_writer._clients[0], LLMObsAgentlessEventClient)

        llmobs_service.disable()


def test_enable_agentless_when_agent_does_not_have_proxy(agent_missing_proxy):
    with override_global_config(dict(_dd_api_key="<not-a-real-api-key>", _llmobs_ml_app="<ml-app-name>")):
        dummy_tracer = DummyTracer()
        llmobs_service.enable(_tracer=dummy_tracer)
        llmobs_instance = llmobs_service._instance
        assert llmobs_instance is not None
        assert llmobs_service.enabled
        assert isinstance(llmobs_instance._llmobs_span_writer._clients[0], LLMObsAgentlessEventClient)

        llmobs_service.disable()


def test_service_disable():
    with override_global_config(dict(_dd_api_key="<not-a-real-api-key>", _llmobs_ml_app="<ml-app-name>")):
        dummy_tracer = DummyTracer()
        llmobs_service.enable(_tracer=dummy_tracer)
        llmobs_service.disable()
        assert llmobs_service.enabled is False
        assert llmobs_service._instance._llmobs_eval_metric_writer.status.value == "stopped"
        assert llmobs_service._instance._llmobs_span_writer.status.value == "stopped"
        assert llmobs_service._instance._evaluator_runner.status.value == "stopped"


def test_service_enable_no_api_key():
    with override_global_config(dict(_dd_api_key="", _llmobs_ml_app="<ml-app-name>")):
        dummy_tracer = DummyTracer()
        with pytest.raises(ValueError):
            llmobs_service.enable(_tracer=dummy_tracer, agentless_enabled=True)
        assert llmobs_service.enabled is False
        assert llmobs_service._instance._llmobs_eval_metric_writer.status.value == "stopped"
        assert llmobs_service._instance._llmobs_span_writer.status.value == "stopped"
        assert llmobs_service._instance._evaluator_runner.status.value == "stopped"


def test_service_enable_already_enabled(mock_llmobs_logs):
    with override_global_config(dict(_dd_api_key="<not-a-real-api-key>", _llmobs_ml_app="<ml-app-name>")):
        dummy_tracer = DummyTracer()
        llmobs_service.enable(_tracer=dummy_tracer)
        llmobs_service.enable(_tracer=dummy_tracer)
        llmobs_instance = llmobs_service._instance
        assert llmobs_instance is not None
        assert llmobs_service.enabled
        assert llmobs_instance.tracer == dummy_tracer
        llmobs_service.disable()
        mock_llmobs_logs.debug.assert_has_calls([mock.call("%s already enabled", "LLMObs")])


@mock.patch("ddtrace.llmobs._llmobs.patch")
def test_service_enable_patches_llmobs_integrations(mock_tracer_patch):
    with override_global_config(dict(_dd_api_key="<not-a-real-api-key>", _llmobs_ml_app="<ml-app-name>")):
        llmobs_service.enable()
        mock_tracer_patch.assert_called_once()
        kwargs = mock_tracer_patch.call_args[1]
        for module in SUPPORTED_LLMOBS_INTEGRATIONS.values():
            assert kwargs[module] is True if module != "botocore" else ["bedrock-runtime"]
        llmobs_service.disable()


@mock.patch("ddtrace.llmobs._llmobs.patch")
def test_service_enable_does_not_override_global_patch_modules(mock_tracer_patch, monkeypatch):
    monkeypatch.setenv("DD_PATCH_MODULES", "openai:false")
    with override_global_config(dict(_dd_api_key="<not-a-real-api-key>", _llmobs_ml_app="<ml-app-name>")):
        llmobs_service.enable()
        mock_tracer_patch.assert_called_once()
        kwargs = mock_tracer_patch.call_args[1]
        for module in SUPPORTED_LLMOBS_INTEGRATIONS.values():
            if module == "openai":
                assert kwargs[module] is False
                continue
            assert kwargs[module] is True if module != "botocore" else ["bedrock-runtime"]
        llmobs_service.disable()


@mock.patch("ddtrace.llmobs._llmobs.patch")
def test_service_enable_does_not_override_integration_enabled_env_vars(mock_tracer_patch, monkeypatch):
    monkeypatch.setenv("DD_TRACE_OPENAI_ENABLED", "false")
    with override_global_config(dict(_dd_api_key="<not-a-real-api-key>", _llmobs_ml_app="<ml-app-name>")):
        llmobs_service.enable()
        mock_tracer_patch.assert_called_once()
        kwargs = mock_tracer_patch.call_args[1]
        for module in SUPPORTED_LLMOBS_INTEGRATIONS.values():
            if module == "openai":
                assert kwargs[module] is False
                continue
            assert kwargs[module] is True if module != "botocore" else ["bedrock-runtime"]
        llmobs_service.disable()


@mock.patch("ddtrace.llmobs._llmobs.patch")
def test_service_enable_does_not_override_global_patch_config(mock_tracer_patch, monkeypatch):
    """Test that _patch_integrations() ensures `DD_PATCH_MODULES` overrides `DD_TRACE_<MODULE>_ENABLED`."""
    monkeypatch.setenv("DD_TRACE_OPENAI_ENABLED", "true")
    monkeypatch.setenv("DD_TRACE_ANTHROPIC_ENABLED", "false")
    monkeypatch.setenv("DD_TRACE_BOTOCORE_ENABLED", "false")
    monkeypatch.setenv("DD_PATCH_MODULES", "openai:false")
    with override_global_config(dict(_dd_api_key="<not-a-real-api-key>", _llmobs_ml_app="<ml-app-name>")):
        llmobs_service.enable()
        mock_tracer_patch.assert_called_once()
        kwargs = mock_tracer_patch.call_args[1]
        for module in SUPPORTED_LLMOBS_INTEGRATIONS.values():
            if module in ("openai", "anthropic", "botocore"):
                assert kwargs[module] is False
                continue
            assert kwargs[module] is True
        llmobs_service.disable()


def test_start_span_with_no_ml_app_throws(llmobs_no_ml_app):
    with pytest.raises(ValueError):
        with llmobs_no_ml_app.task():
            pass


def test_start_span_while_disabled_logs_warning(llmobs, mock_llmobs_logs):
    llmobs.disable()
    _ = llmobs.llm(model_name="test_model", name="test_llm_call", model_provider="test_provider")
    mock_llmobs_logs.warning.assert_called_once_with(SPAN_START_WHILE_DISABLED_WARNING)
    mock_llmobs_logs.reset_mock()
    _ = llmobs.tool(name="test_tool")
    mock_llmobs_logs.warning.assert_called_once_with(SPAN_START_WHILE_DISABLED_WARNING)
    mock_llmobs_logs.reset_mock()
    _ = llmobs.task(name="test_task")
    mock_llmobs_logs.warning.assert_called_once_with(SPAN_START_WHILE_DISABLED_WARNING)
    mock_llmobs_logs.reset_mock()
    _ = llmobs.workflow(name="test_workflow")
    mock_llmobs_logs.warning.assert_called_once_with(SPAN_START_WHILE_DISABLED_WARNING)
    mock_llmobs_logs.reset_mock()
    _ = llmobs.agent(name="test_agent")
    mock_llmobs_logs.warning.assert_called_once_with(SPAN_START_WHILE_DISABLED_WARNING)


def test_start_span_uses_kind_as_default_name(llmobs):
    with llmobs.llm(model_name="test_model", model_provider="test_provider") as span:
        assert span.name == "llm"
    with llmobs.tool() as span:
        assert span.name == "tool"
    with llmobs.task() as span:
        assert span.name == "task"
    with llmobs.workflow() as span:
        assert span.name == "workflow"
    with llmobs.agent() as span:
        assert span.name == "agent"


def test_start_span_with_session_id(llmobs):
    with llmobs.llm(model_name="test_model", session_id="test_session_id") as span:
        assert span._get_ctx_item(SESSION_ID) == "test_session_id"
    with llmobs.tool(session_id="test_session_id") as span:
        assert span._get_ctx_item(SESSION_ID) == "test_session_id"
    with llmobs.task(session_id="test_session_id") as span:
        assert span._get_ctx_item(SESSION_ID) == "test_session_id"
    with llmobs.workflow(session_id="test_session_id") as span:
        assert span._get_ctx_item(SESSION_ID) == "test_session_id"
    with llmobs.agent(session_id="test_session_id") as span:
        assert span._get_ctx_item(SESSION_ID) == "test_session_id"


def test_session_id_becomes_top_level_field(llmobs, llmobs_events):
    session_id = "test_session_id"
    with llmobs.task(session_id=session_id) as span:
        pass
    assert len(llmobs_events) == 1
    assert llmobs_events[0] == _expected_llmobs_non_llm_span_event(span, "task", session_id=session_id)


def test_llm_span(llmobs, llmobs_events):
    with llmobs.llm(model_name="test_model", name="test_llm_call", model_provider="test_provider") as span:
        assert span.name == "test_llm_call"
        assert span.resource == "llm"
        assert span.span_type == "llm"
        assert span._get_ctx_item(SPAN_KIND) == "llm"
        assert span._get_ctx_item(MODEL_NAME) == "test_model"
        assert span._get_ctx_item(MODEL_PROVIDER) == "test_provider"
    assert len(llmobs_events) == 1
    assert llmobs_events[0] == _expected_llmobs_llm_span_event(
        span, "llm", model_name="test_model", model_provider="test_provider"
    )


def test_llm_span_no_model_sets_default(llmobs, llmobs_events):
    with llmobs.llm(name="test_llm_call", model_provider="test_provider") as span:
        assert span._get_ctx_item(MODEL_NAME) == "custom"
    assert len(llmobs_events) == 1
    assert llmobs_events[0] == _expected_llmobs_llm_span_event(
        span, "llm", model_name="custom", model_provider="test_provider"
    )


def test_default_model_provider_set_to_custom(llmobs):
    with llmobs.llm(model_name="test_model", name="test_llm_call") as span:
        assert span.name == "test_llm_call"
        assert span.resource == "llm"
        assert span.span_type == "llm"
        assert span._get_ctx_item(SPAN_KIND) == "llm"
        assert span._get_ctx_item(MODEL_NAME) == "test_model"
        assert span._get_ctx_item(MODEL_PROVIDER) == "custom"


def test_tool_span(llmobs, llmobs_events):
    with llmobs.tool(name="test_tool") as span:
        assert span.name == "test_tool"
        assert span.resource == "tool"
        assert span.span_type == "llm"
        assert span._get_ctx_item(SPAN_KIND) == "tool"
    assert len(llmobs_events) == 1
    assert llmobs_events[0] == _expected_llmobs_non_llm_span_event(span, "tool")


def test_task_span(llmobs, llmobs_events):
    with llmobs.task(name="test_task") as span:
        assert span.name == "test_task"
        assert span.resource == "task"
        assert span.span_type == "llm"
        assert span._get_ctx_item(SPAN_KIND) == "task"
    assert len(llmobs_events) == 1
    assert llmobs_events[0] == _expected_llmobs_non_llm_span_event(span, "task")


def test_workflow_span(llmobs, llmobs_events):
    with llmobs.workflow(name="test_workflow") as span:
        assert span.name == "test_workflow"
        assert span.resource == "workflow"
        assert span.span_type == "llm"
        assert span._get_ctx_item(SPAN_KIND) == "workflow"
    assert len(llmobs_events) == 1
    assert llmobs_events[0] == _expected_llmobs_non_llm_span_event(span, "workflow")


def test_agent_span(llmobs, llmobs_events):
    with llmobs.agent(name="test_agent") as span:
        assert span.name == "test_agent"
        assert span.resource == "agent"
        assert span.span_type == "llm"
        assert span._get_ctx_item(SPAN_KIND) == "agent"
    assert len(llmobs_events) == 1
    assert llmobs_events[0] == _expected_llmobs_llm_span_event(span, "agent")


def test_embedding_span_no_model_sets_default(llmobs, llmobs_events):
    with llmobs.embedding(name="test_embedding", model_provider="test_provider") as span:
        assert span._get_ctx_item(MODEL_NAME) == "custom"
    assert len(llmobs_events) == 1
    assert llmobs_events[0] == _expected_llmobs_llm_span_event(
        span, "embedding", model_name="custom", model_provider="test_provider"
    )


def test_embedding_default_model_provider_set_to_custom(llmobs):
    with llmobs.embedding(model_name="test_model", name="test_embedding") as span:
        assert span.name == "test_embedding"
        assert span.resource == "embedding"
        assert span.span_type == "llm"
        assert span._get_ctx_item(SPAN_KIND) == "embedding"
        assert span._get_ctx_item(MODEL_NAME) == "test_model"
        assert span._get_ctx_item(MODEL_PROVIDER) == "custom"


def test_embedding_span(llmobs, llmobs_events):
    with llmobs.embedding(model_name="test_model", name="test_embedding", model_provider="test_provider") as span:
        assert span.name == "test_embedding"
        assert span.resource == "embedding"
        assert span.span_type == "llm"
        assert span._get_ctx_item(SPAN_KIND) == "embedding"
        assert span._get_ctx_item(MODEL_NAME) == "test_model"
        assert span._get_ctx_item(MODEL_PROVIDER) == "test_provider"
    assert len(llmobs_events) == 1
    assert llmobs_events[0] == _expected_llmobs_llm_span_event(
        span, "embedding", model_name="test_model", model_provider="test_provider"
    )


def test_annotate_no_active_span_logs_warning(llmobs, mock_llmobs_logs):
    llmobs.annotate(metadata={"test": "test"})
    mock_llmobs_logs.warning.assert_called_once_with("No span provided and no active LLMObs-generated span found.")


def test_annotate_non_llm_span_logs_warning(llmobs, mock_llmobs_logs):
    dummy_tracer = DummyTracer()
    with dummy_tracer.trace("root") as non_llmobs_span:
        llmobs.annotate(span=non_llmobs_span, metadata={"test": "test"})
        mock_llmobs_logs.warning.assert_called_once_with("Span must be an LLMObs-generated span.")


def test_annotate_finished_span_does_nothing(llmobs, mock_llmobs_logs):
    with llmobs.llm(model_name="test_model", name="test_llm_call", model_provider="test_provider") as span:
        pass
    llmobs.annotate(span=span, metadata={"test": "test"})
    mock_llmobs_logs.warning.assert_called_once_with("Cannot annotate a finished span.")


def test_annotate_metadata(llmobs):
    with llmobs.llm(model_name="test_model", name="test_llm_call", model_provider="test_provider") as span:
        llmobs.annotate(span=span, metadata={"temperature": 0.5, "max_tokens": 20, "top_k": 10, "n": 3})
        assert span._get_ctx_item(METADATA) == {"temperature": 0.5, "max_tokens": 20, "top_k": 10, "n": 3}


def test_annotate_metadata_updates(llmobs):
    with llmobs.llm(model_name="test_model", name="test_llm_call", model_provider="test_provider") as span:
        llmobs.annotate(span=span, metadata={"temperature": 0.5, "max_tokens": 20, "top_k": 10, "n": 3})
        llmobs.annotate(span=span, metadata={"temperature": 1, "logit_bias": [{"1": 2}]})
        assert span._get_ctx_item(METADATA) == {
            "temperature": 1,
            "max_tokens": 20,
            "top_k": 10,
            "n": 3,
            "logit_bias": [{"1": 2}],
        }


def test_annotate_metadata_wrong_type_raises_warning(llmobs, mock_llmobs_logs):
    with llmobs.llm(model_name="test_model", name="test_llm_call", model_provider="test_provider") as span:
        llmobs.annotate(span=span, metadata="wrong_metadata")
        assert span._get_ctx_item(METADATA) is None
        mock_llmobs_logs.warning.assert_called_once_with("metadata must be a dictionary")
        mock_llmobs_logs.reset_mock()


def test_annotate_tag(llmobs):
    with llmobs.llm(model_name="test_model", name="test_llm_call", model_provider="test_provider") as span:
        llmobs.annotate(span=span, tags={"test_tag_name": "test_tag_value", "test_numeric_tag": 10})
        assert span._get_ctx_item(TAGS) == {"test_tag_name": "test_tag_value", "test_numeric_tag": 10}


def test_annotate_tag_wrong_type(llmobs, mock_llmobs_logs):
    with llmobs.llm(model_name="test_model", name="test_llm_call", model_provider="test_provider") as span:
        llmobs.annotate(span=span, tags=12345)
        assert span._get_ctx_item(TAGS) is None
        mock_llmobs_logs.warning.assert_called_once_with(
            "span tags must be a dictionary of string key - primitive value pairs."
        )


def test_annotate_input_string(llmobs):
    with llmobs.llm(model_name="test_model") as llm_span:
        llmobs.annotate(span=llm_span, input_data="test_input")
        assert llm_span._get_ctx_item(INPUT_MESSAGES) == [{"content": "test_input"}]
    with llmobs.task() as task_span:
        llmobs.annotate(span=task_span, input_data="test_input")
        assert task_span._get_ctx_item(INPUT_VALUE) == "test_input"
    with llmobs.tool() as tool_span:
        llmobs.annotate(span=tool_span, input_data="test_input")
        assert tool_span._get_ctx_item(INPUT_VALUE) == "test_input"
    with llmobs.workflow() as workflow_span:
        llmobs.annotate(span=workflow_span, input_data="test_input")
        assert workflow_span._get_ctx_item(INPUT_VALUE) == "test_input"
    with llmobs.agent() as agent_span:
        llmobs.annotate(span=agent_span, input_data="test_input")
        assert agent_span._get_ctx_item(INPUT_VALUE) == "test_input"
    with llmobs.retrieval() as retrieval_span:
        llmobs.annotate(span=retrieval_span, input_data="test_input")
        assert retrieval_span._get_ctx_item(INPUT_VALUE) == "test_input"


def test_annotate_numeric_io(llmobs):
    with llmobs.task() as task_span:
        llmobs.annotate(span=task_span, input_data=0, output_data=0)
        assert task_span._get_ctx_item(INPUT_VALUE) == "0"
        assert task_span._get_ctx_item(OUTPUT_VALUE) == "0"
    with llmobs.task() as task_span:
        llmobs.annotate(span=task_span, input_data=1.23, output_data=1.23)
        assert task_span._get_ctx_item(INPUT_VALUE) == "1.23"
        assert task_span._get_ctx_item(OUTPUT_VALUE) == "1.23"


def test_annotate_input_serializable_value(llmobs):
    with llmobs.task() as task_span:
        llmobs.annotate(span=task_span, input_data=["test_input"])
        assert task_span._get_ctx_item(INPUT_VALUE) == '["test_input"]'
    with llmobs.tool() as tool_span:
        llmobs.annotate(span=tool_span, input_data={"test_input": "hello world"})
        assert tool_span._get_ctx_item(INPUT_VALUE) == '{"test_input": "hello world"}'
    with llmobs.workflow() as workflow_span:
        llmobs.annotate(span=workflow_span, input_data=("asd", 123))
        assert workflow_span._get_ctx_item(INPUT_VALUE) == '["asd", 123]'
    with llmobs.agent() as agent_span:
        llmobs.annotate(span=agent_span, input_data="test_input")
        assert agent_span._get_ctx_item(INPUT_VALUE) == "test_input"
    with llmobs.retrieval() as retrieval_span:
        llmobs.annotate(span=retrieval_span, input_data=[0, 1, 2, 3, 4])
        assert retrieval_span._get_ctx_item(INPUT_VALUE) == str([0, 1, 2, 3, 4])


def test_annotate_input_llm_message(llmobs):
    with llmobs.llm(model_name="test_model") as span:
        llmobs.annotate(span=span, input_data=[{"content": "test_input", "role": "human"}])
        assert span._get_ctx_item(INPUT_MESSAGES) == [{"content": "test_input", "role": "human"}]


def test_annotate_input_llm_message_wrong_type(llmobs, mock_llmobs_logs):
    with llmobs.llm(model_name="test_model") as span:
        llmobs.annotate(span=span, input_data=[{"content": object()}])
        assert span._get_ctx_item(INPUT_MESSAGES) is None
        mock_llmobs_logs.warning.assert_called_once_with("Failed to parse input messages.", exc_info=True)


def test_llmobs_annotate_incorrect_message_content_type_raises_warning(llmobs, mock_llmobs_logs):
    with llmobs.llm(model_name="test_model") as span:
        llmobs.annotate(span=span, input_data={"role": "user", "content": {"nested": "yes"}})
        mock_llmobs_logs.warning.assert_called_once_with("Failed to parse input messages.", exc_info=True)
        mock_llmobs_logs.reset_mock()
        llmobs.annotate(span=span, output_data={"role": "user", "content": {"nested": "yes"}})
        mock_llmobs_logs.warning.assert_called_once_with("Failed to parse output messages.", exc_info=True)


def test_annotate_document_str(llmobs):
    with llmobs.embedding(model_name="test_model") as span:
        llmobs.annotate(span=span, input_data="test_document_text")
        documents = span._get_ctx_item(INPUT_DOCUMENTS)
        assert documents
        assert len(documents) == 1
        assert documents[0]["text"] == "test_document_text"
    with llmobs.retrieval() as span:
        llmobs.annotate(span=span, output_data="test_document_text")
        documents = span._get_ctx_item(OUTPUT_DOCUMENTS)
        assert documents
        assert len(documents) == 1
        assert documents[0]["text"] == "test_document_text"


def test_annotate_document_dict(llmobs):
    with llmobs.embedding(model_name="test_model") as span:
        llmobs.annotate(span=span, input_data={"text": "test_document_text"})
        documents = span._get_ctx_item(INPUT_DOCUMENTS)
        assert documents
        assert len(documents) == 1
        assert documents[0]["text"] == "test_document_text"
    with llmobs.retrieval() as span:
        llmobs.annotate(span=span, output_data={"text": "test_document_text"})
        documents = span._get_ctx_item(OUTPUT_DOCUMENTS)
        assert documents
        assert len(documents) == 1
        assert documents[0]["text"] == "test_document_text"


def test_annotate_document_list(llmobs):
    with llmobs.embedding(model_name="test_model") as span:
        llmobs.annotate(
            span=span,
            input_data=[{"text": "test_document_text"}, {"text": "text", "name": "name", "score": 0.9, "id": "id"}],
        )
        documents = span._get_ctx_item(INPUT_DOCUMENTS)
        assert documents
        assert len(documents) == 2
        assert documents[0]["text"] == "test_document_text"
        assert documents[1]["text"] == "text"
        assert documents[1]["name"] == "name"
        assert documents[1]["id"] == "id"
        assert documents[1]["score"] == 0.9
    with llmobs.retrieval() as span:
        llmobs.annotate(
            span=span,
            output_data=[{"text": "test_document_text"}, {"text": "text", "name": "name", "score": 0.9, "id": "id"}],
        )
        documents = span._get_ctx_item(OUTPUT_DOCUMENTS)
        assert documents
        assert len(documents) == 2
        assert documents[0]["text"] == "test_document_text"
        assert documents[1]["text"] == "text"
        assert documents[1]["name"] == "name"
        assert documents[1]["id"] == "id"
        assert documents[1]["score"] == 0.9


def test_annotate_incorrect_document_type_raises_warning(llmobs, mock_llmobs_logs):
    with llmobs.embedding(model_name="test_model") as span:
        llmobs.annotate(span=span, input_data={"text": 123})
        mock_llmobs_logs.warning.assert_called_once_with("Failed to parse input documents.", exc_info=True)
        mock_llmobs_logs.reset_mock()
        llmobs.annotate(span=span, input_data=123)
        mock_llmobs_logs.warning.assert_called_once_with("Failed to parse input documents.", exc_info=True)
        mock_llmobs_logs.reset_mock()
        llmobs.annotate(span=span, input_data=object())
        mock_llmobs_logs.warning.assert_called_once_with("Failed to parse input documents.", exc_info=True)
        mock_llmobs_logs.reset_mock()
    with llmobs.retrieval() as span:
        llmobs.annotate(span=span, output_data=[{"score": 0.9, "id": "id", "name": "name"}])
        mock_llmobs_logs.warning.assert_called_once_with("Failed to parse output documents.", exc_info=True)
        mock_llmobs_logs.reset_mock()
        llmobs.annotate(span=span, output_data=123)
        mock_llmobs_logs.warning.assert_called_once_with("Failed to parse output documents.", exc_info=True)
        mock_llmobs_logs.reset_mock()
        llmobs.annotate(span=span, output_data=object())
        mock_llmobs_logs.warning.assert_called_once_with("Failed to parse output documents.", exc_info=True)


def test_annotate_document_no_text_raises_warning(llmobs, mock_llmobs_logs):
    with llmobs.embedding(model_name="test_model") as span:
        llmobs.annotate(span=span, input_data=[{"score": 0.9, "id": "id", "name": "name"}])
        mock_llmobs_logs.warning.assert_called_once_with("Failed to parse input documents.", exc_info=True)
    mock_llmobs_logs.reset_mock()
    with llmobs.retrieval() as span:
        llmobs.annotate(span=span, output_data=[{"score": 0.9, "id": "id", "name": "name"}])
        mock_llmobs_logs.warning.assert_called_once_with("Failed to parse output documents.", exc_info=True)


def test_annotate_incorrect_document_field_type_raises_warning(llmobs, mock_llmobs_logs):
    with llmobs.embedding(model_name="test_model") as span:
        llmobs.annotate(span=span, input_data=[{"text": "test_document_text", "score": "0.9"}])
        mock_llmobs_logs.warning.assert_called_once_with("Failed to parse input documents.", exc_info=True)
    mock_llmobs_logs.reset_mock()
    with llmobs.embedding(model_name="test_model") as span:
        llmobs.annotate(
            span=span, input_data=[{"text": "text", "id": 123, "score": "0.9", "name": ["h", "e", "l", "l", "o"]}]
        )
        mock_llmobs_logs.warning.assert_called_once_with("Failed to parse input documents.", exc_info=True)
    mock_llmobs_logs.reset_mock()
    with llmobs.retrieval() as span:
        llmobs.annotate(span=span, output_data=[{"text": "test_document_text", "score": "0.9"}])
        mock_llmobs_logs.warning.assert_called_once_with("Failed to parse output documents.", exc_info=True)
    mock_llmobs_logs.reset_mock()
    with llmobs.retrieval() as span:
        llmobs.annotate(
            span=span, output_data=[{"text": "text", "id": 123, "score": "0.9", "name": ["h", "e", "l", "l", "o"]}]
        )
        mock_llmobs_logs.warning.assert_called_once_with("Failed to parse output documents.", exc_info=True)


def test_annotate_output_string(llmobs):
    with llmobs.llm(model_name="test_model") as llm_span:
        llmobs.annotate(span=llm_span, output_data="test_output")
        assert llm_span._get_ctx_item(OUTPUT_MESSAGES) == [{"content": "test_output"}]
    with llmobs.embedding(model_name="test_model") as embedding_span:
        llmobs.annotate(span=embedding_span, output_data="test_output")
        assert embedding_span._get_ctx_item(OUTPUT_VALUE) == "test_output"
    with llmobs.task() as task_span:
        llmobs.annotate(span=task_span, output_data="test_output")
        assert task_span._get_ctx_item(OUTPUT_VALUE) == "test_output"
    with llmobs.tool() as tool_span:
        llmobs.annotate(span=tool_span, output_data="test_output")
        assert tool_span._get_ctx_item(OUTPUT_VALUE) == "test_output"
    with llmobs.workflow() as workflow_span:
        llmobs.annotate(span=workflow_span, output_data="test_output")
        assert workflow_span._get_ctx_item(OUTPUT_VALUE) == "test_output"
    with llmobs.agent() as agent_span:
        llmobs.annotate(span=agent_span, output_data="test_output")
        assert agent_span._get_ctx_item(OUTPUT_VALUE) == "test_output"


def test_annotate_output_serializable_value(llmobs):
    with llmobs.embedding(model_name="test_model") as embedding_span:
        llmobs.annotate(span=embedding_span, output_data=[[0, 1, 2, 3], [4, 5, 6, 7]])
        assert embedding_span._get_ctx_item(OUTPUT_VALUE) == "[[0, 1, 2, 3], [4, 5, 6, 7]]"
    with llmobs.task() as task_span:
        llmobs.annotate(span=task_span, output_data=["test_output"])
        assert task_span._get_ctx_item(OUTPUT_VALUE) == '["test_output"]'
    with llmobs.tool() as tool_span:
        llmobs.annotate(span=tool_span, output_data={"test_output": "hello world"})
        assert tool_span._get_ctx_item(OUTPUT_VALUE) == '{"test_output": "hello world"}'
    with llmobs.workflow() as workflow_span:
        llmobs.annotate(span=workflow_span, output_data=("asd", 123))
        assert workflow_span._get_ctx_item(OUTPUT_VALUE) == '["asd", 123]'
    with llmobs.agent() as agent_span:
        llmobs.annotate(span=agent_span, output_data="test_output")
        assert agent_span._get_ctx_item(OUTPUT_VALUE) == "test_output"


def test_annotate_output_llm_message(llmobs):
    with llmobs.llm(model_name="test_model") as llm_span:
        llmobs.annotate(span=llm_span, output_data=[{"content": "test_output", "role": "human"}])
        assert llm_span._get_ctx_item(OUTPUT_MESSAGES) == [{"content": "test_output", "role": "human"}]


def test_annotate_output_llm_message_wrong_type(llmobs, mock_llmobs_logs):
    with llmobs.llm(model_name="test_model") as llm_span:
        llmobs.annotate(span=llm_span, output_data=[{"content": object()}])
        assert llm_span._get_ctx_item(OUTPUT_MESSAGES) is None
        mock_llmobs_logs.warning.assert_called_once_with("Failed to parse output messages.", exc_info=True)


def test_annotate_metrics(llmobs):
    with llmobs.llm(model_name="test_model") as span:
        llmobs.annotate(span=span, metrics={"input_tokens": 10, "output_tokens": 20, "total_tokens": 30})
        assert span._get_ctx_item(METRICS) == {"input_tokens": 10, "output_tokens": 20, "total_tokens": 30}


def test_annotate_metrics_updates(llmobs):
    with llmobs.llm(model_name="test_model") as span:
        llmobs.annotate(span=span, metrics={"input_tokens": 10, "output_tokens": 20})
        llmobs.annotate(span=span, metrics={"input_tokens": 20, "total_tokens": 40})
        assert span._get_ctx_item(METRICS) == {"input_tokens": 20, "output_tokens": 20, "total_tokens": 40}


def test_annotate_metrics_wrong_type(llmobs, mock_llmobs_logs):
    with llmobs.llm(model_name="test_model") as llm_span:
        llmobs.annotate(span=llm_span, metrics=12345)
        assert llm_span._get_ctx_item(METRICS) is None
        mock_llmobs_logs.warning.assert_called_once_with(
            "metrics must be a dictionary of string key - numeric value pairs."
        )
        mock_llmobs_logs.reset_mock()


def test_annotate_prompt_dict(llmobs):
    with llmobs.llm(model_name="test_model") as span:
        llmobs.annotate(
            span=span,
            prompt={
                "template": "{var1} {var3}",
                "variables": {"var1": "var1", "var2": "var3"},
                "version": "1.0.0",
                "id": "test_prompt",
            },
        )
        assert span._get_ctx_item(INPUT_PROMPT) == {
            "template": "{var1} {var3}",
            "variables": {"var1": "var1", "var2": "var3"},
            "version": "1.0.0",
            "id": "test_prompt",
            "_dd_context_variable_keys": ["context"],
            "_dd_query_variable_keys": ["question"],
        }


def test_annotate_prompt_dict_with_context_var_keys(llmobs):
    with llmobs.llm(model_name="test_model") as span:
        llmobs.annotate(
            span=span,
            prompt={
                "template": "{var1} {var3}",
                "variables": {"var1": "var1", "var2": "var3"},
                "version": "1.0.0",
                "id": "test_prompt",
                "rag_context_variables": ["var1", "var2"],
                "rag_query_variables": ["user_input"],
            },
        )
        assert span._get_ctx_item(INPUT_PROMPT) == {
            "template": "{var1} {var3}",
            "variables": {"var1": "var1", "var2": "var3"},
            "version": "1.0.0",
            "id": "test_prompt",
            "_dd_context_variable_keys": ["var1", "var2"],
            "_dd_query_variable_keys": ["user_input"],
        }


def test_annotate_prompt_typed_dict(llmobs):
    with llmobs.llm(model_name="test_model") as span:
        llmobs.annotate(
            span=span,
            prompt=Prompt(
                template="{var1} {var3}",
                variables={"var1": "var1", "var2": "var3"},
                version="1.0.0",
                id="test_prompt",
                rag_context_variables=["var1", "var2"],
                rag_query_variables=["user_input"],
            ),
        )
        assert span._get_ctx_item(INPUT_PROMPT) == {
            "template": "{var1} {var3}",
            "variables": {"var1": "var1", "var2": "var3"},
            "version": "1.0.0",
            "id": "test_prompt",
            "_dd_context_variable_keys": ["var1", "var2"],
            "_dd_query_variable_keys": ["user_input"],
        }


def test_annotate_prompt_wrong_type(llmobs, mock_llmobs_logs):
    with llmobs.llm(model_name="test_model") as span:
        llmobs.annotate(span=span, prompt="prompt")
        assert span._get_ctx_item(INPUT_PROMPT) is None
        mock_llmobs_logs.warning.assert_called_once_with("Failed to validate prompt with error: ", exc_info=True)
        mock_llmobs_logs.reset_mock()

        llmobs.annotate(span=span, prompt={"template": 1})
        mock_llmobs_logs.warning.assert_called_once_with("Failed to validate prompt with error: ", exc_info=True)
        mock_llmobs_logs.reset_mock()


def test_span_error_sets_error(llmobs, llmobs_events):
    with pytest.raises(ValueError):
        with llmobs.llm(model_name="test_model", model_provider="test_model_provider") as span:
            raise ValueError("test error message")
    assert len(llmobs_events) == 1
    assert llmobs_events[0] == _expected_llmobs_llm_span_event(
        span,
        model_name="test_model",
        model_provider="test_model_provider",
        error="builtins.ValueError",
        error_message="test error message",
        error_stack=span.get_tag("error.stack"),
    )


@pytest.mark.parametrize(
    "ddtrace_global_config",
    [dict(version="1.2.3", env="test_env", service="test_service", _llmobs_ml_app="test_app_name")],
)
def test_tags(ddtrace_global_config, llmobs, llmobs_events, monkeypatch):
    with llmobs.task(name="test_task") as span:
        pass
    assert len(llmobs_events) == 1
    assert llmobs_events[0] == _expected_llmobs_non_llm_span_event(
        span,
        "task",
        tags={"version": "1.2.3", "env": "test_env", "service": "test_service", "ml_app": "test_app_name"},
    )


def test_ml_app_override(llmobs, llmobs_events):
    with llmobs.task(name="test_task", ml_app="test_app") as span:
        pass
    assert len(llmobs_events) == 1
    assert llmobs_events[0] == _expected_llmobs_non_llm_span_event(span, "task", tags={"ml_app": "test_app"})
    with llmobs.tool(name="test_tool", ml_app="test_app") as span:
        pass
    assert len(llmobs_events) == 2
    assert llmobs_events[1] == _expected_llmobs_non_llm_span_event(span, "tool", tags={"ml_app": "test_app"})
    with llmobs.llm(model_name="model_name", name="test_llm", ml_app="test_app") as span:
        pass
    assert len(llmobs_events) == 3
    assert llmobs_events[2] == _expected_llmobs_llm_span_event(
        span, "llm", model_name="model_name", model_provider="custom", tags={"ml_app": "test_app"}
    )
    with llmobs.embedding(model_name="model_name", name="test_embedding", ml_app="test_app") as span:
        pass
    assert len(llmobs_events) == 4
    assert llmobs_events[3] == _expected_llmobs_llm_span_event(
        span, "embedding", model_name="model_name", model_provider="custom", tags={"ml_app": "test_app"}
    )
    with llmobs.workflow(name="test_workflow", ml_app="test_app") as span:
        pass
    assert len(llmobs_events) == 5
    assert llmobs_events[4] == _expected_llmobs_non_llm_span_event(span, "workflow", tags={"ml_app": "test_app"})
    with llmobs.agent(name="test_agent", ml_app="test_app") as span:
        pass
    assert len(llmobs_events) == 6
    assert llmobs_events[5] == _expected_llmobs_llm_span_event(span, "agent", tags={"ml_app": "test_app"})
    with llmobs.retrieval(name="test_retrieval", ml_app="test_app") as span:
        pass
    assert len(llmobs_events) == 7
    assert llmobs_events[6] == _expected_llmobs_non_llm_span_event(span, "retrieval", tags={"ml_app": "test_app"})


def test_export_span_specified_span_is_incorrect_type_raises_warning(llmobs, mock_llmobs_logs):
    llmobs.export_span(span="asd")
    mock_llmobs_logs.warning.assert_called_once_with("Failed to export span. Span must be a valid Span object.")


def test_export_span_specified_span_is_not_llmobs_span_raises_warning(llmobs, mock_llmobs_logs):
    with DummyTracer().trace("non_llmobs_span") as span:
        llmobs.export_span(span=span)
    mock_llmobs_logs.warning.assert_called_once_with("Span must be an LLMObs-generated span.")


def test_export_span_specified_span_returns_span_context(llmobs):
    with llmobs.llm(model_name="test_model", name="test_llm_call", model_provider="test_provider") as span:
        span_context = llmobs.export_span(span=span)
        assert span_context is not None
        assert span_context["span_id"] == str(span.span_id)
        assert span_context["trace_id"] == format_trace_id(span.trace_id)


def test_export_span_no_specified_span_no_active_span_raises_warning(llmobs, mock_llmobs_logs):
    llmobs.export_span()
    mock_llmobs_logs.warning.assert_called_once_with("No span provided and no active LLMObs-generated span found.")


def test_export_span_active_span_not_llmobs_span_raises_warning(llmobs, mock_llmobs_logs):
    with llmobs._instance.tracer.trace("non_llmobs_span"):
        llmobs.export_span()
    mock_llmobs_logs.warning.assert_called_once_with("No span provided and no active LLMObs-generated span found.")


def test_export_span_no_specified_span_returns_exported_active_span(llmobs):
    with llmobs.llm(model_name="test_model", name="test_llm_call", model_provider="test_provider") as span:
        span_context = llmobs.export_span()
        assert span_context is not None
        assert span_context["span_id"] == str(span.span_id)
        assert span_context["trace_id"] == format_trace_id(span.trace_id)


def test_submit_evaluation_no_api_key_raises_warning(llmobs, mock_llmobs_logs):
    with override_global_config(dict(_dd_api_key="")):
        llmobs.submit_evaluation(
            span_context={"span_id": "123", "trace_id": "456"},
            label="toxicity",
            metric_type="categorical",
            value="high",
        )
        mock_llmobs_logs.warning.assert_called_once_with(
            "DD_API_KEY is required for sending evaluation metrics. Evaluation metric data will not be sent. "
            "Ensure this configuration is set before running your application."
        )


def test_submit_evaluation_ml_app_raises_warning(llmobs, mock_llmobs_logs):
    with override_global_config(dict(_llmobs_ml_app="")):
        llmobs.submit_evaluation(
            span_context={"span_id": "123", "trace_id": "456"},
            label="toxicity",
            metric_type="categorical",
            value="high",
        )
        mock_llmobs_logs.warning.assert_called_once_with(
            "ML App name is required for sending evaluation metrics. Evaluation metric data will not be sent. "
            "Ensure this configuration is set before running your application."
        )


def test_submit_evaluation_span_context_incorrect_type_raises_warning(llmobs, mock_llmobs_logs):
    llmobs.submit_evaluation(span_context="asd", label="toxicity", metric_type="categorical", value="high")
    mock_llmobs_logs.warning.assert_called_once_with(
        "span_context must be a dictionary containing both span_id and trace_id keys. "
        "LLMObs.export_span() can be used to generate this dictionary from a given span."
    )


def test_submit_evaluation_empty_span_or_trace_id_raises_warning(llmobs, mock_llmobs_logs):
    llmobs.submit_evaluation(
        span_context={"trace_id": "456"}, label="toxicity", metric_type="categorical", value="high"
    )
    mock_llmobs_logs.warning.assert_called_once_with(
        "span_id and trace_id must both be specified for the given evaluation metric to be submitted."
    )
    mock_llmobs_logs.reset_mock()
    llmobs.submit_evaluation(span_context={"span_id": "456"}, label="toxicity", metric_type="categorical", value="high")
    mock_llmobs_logs.warning.assert_called_once_with(
        "span_id and trace_id must both be specified for the given evaluation metric to be submitted."
    )


def test_submit_evaluation_invalid_timestamp_raises_warning(llmobs, mock_llmobs_logs):
    llmobs.submit_evaluation(
        span_context={"span_id": "123", "trace_id": "456"},
        label="",
        metric_type="categorical",
        value="high",
        ml_app="dummy",
        timestamp_ms="invalid",
    )
    mock_llmobs_logs.warning.assert_called_once_with(
        "timestamp_ms must be a non-negative integer. Evaluation metric data will not be sent"
    )


def test_submit_evaluation_empty_label_raises_warning(llmobs, mock_llmobs_logs):
    llmobs.submit_evaluation(
        span_context={"span_id": "123", "trace_id": "456"}, label="", metric_type="categorical", value="high"
    )
    mock_llmobs_logs.warning.assert_called_once_with("label must be the specified name of the evaluation metric.")


def test_submit_evaluation_incorrect_metric_type_raises_warning(llmobs, mock_llmobs_logs):
    llmobs.submit_evaluation(
        span_context={"span_id": "123", "trace_id": "456"}, label="toxicity", metric_type="wrong", value="high"
    )
    mock_llmobs_logs.warning.assert_called_once_with("metric_type must be one of 'categorical' or 'score'.")
    mock_llmobs_logs.reset_mock()
    llmobs.submit_evaluation(
        span_context={"span_id": "123", "trace_id": "456"}, label="toxicity", metric_type="", value="high"
    )
    mock_llmobs_logs.warning.assert_called_once_with("metric_type must be one of 'categorical' or 'score'.")


def test_submit_evaluation_numerical_value_raises_unsupported_warning(llmobs, mock_llmobs_logs):
    llmobs.submit_evaluation(
        span_context={"span_id": "123", "trace_id": "456"}, label="token_count", metric_type="numerical", value="high"
    )
    mock_llmobs_logs.warning.assert_has_calls(
        [
            mock.call(
                "The evaluation metric type 'numerical' is unsupported. Use 'score' instead. "
                "Converting `numerical` metric to `score` type."
            ),
        ]
    )


def test_submit_evaluation_incorrect_numerical_value_type_raises_warning(llmobs, mock_llmobs_logs):
    llmobs.submit_evaluation(
        span_context={"span_id": "123", "trace_id": "456"}, label="token_count", metric_type="numerical", value="high"
    )
    mock_llmobs_logs.warning.assert_has_calls(
        [
            mock.call("value must be an integer or float for a score metric."),
        ]
    )


def test_submit_evaluation_incorrect_score_value_type_raises_warning(llmobs, mock_llmobs_logs):
    llmobs.submit_evaluation(
        span_context={"span_id": "123", "trace_id": "456"}, label="token_count", metric_type="score", value="high"
    )
    mock_llmobs_logs.warning.assert_called_once_with("value must be an integer or float for a score metric.")


def test_submit_evaluation_invalid_tags_raises_warning(llmobs, mock_llmobs_logs):
    llmobs.submit_evaluation(
        span_context={"span_id": "123", "trace_id": "456"},
        label="toxicity",
        metric_type="categorical",
        value="high",
        tags=["invalid"],
    )
    mock_llmobs_logs.warning.assert_called_once_with("tags must be a dictionary of string key-value pairs.")


def test_submit_evaluation_invalid_metadata_raises_warning(llmobs, mock_llmobs_logs):
    llmobs.submit_evaluation(
        span_context={"span_id": "123", "trace_id": "456"},
        label="toxicity",
        metric_type="categorical",
        value="high",
        metadata=1,
    )
    mock_llmobs_logs.warning.assert_called_once_with("metadata must be json serializable dictionary.")


@pytest.mark.parametrize(
    "ddtrace_global_config",
    [dict(_llmobs_ml_app="test_app_name")],
)
def test_submit_evaluation_non_string_tags_raises_warning_but_still_submits(
    llmobs, mock_llmobs_logs, mock_llmobs_eval_metric_writer
):
    llmobs.submit_evaluation(
        span_context={"span_id": "123", "trace_id": "456"},
        label="toxicity",
        metric_type="categorical",
        value="high",
        tags={1: 2, "foo": "bar"},
        ml_app="dummy",
    )
    mock_llmobs_logs.warning.assert_called_once_with(
        "Failed to parse tags. Tags for evaluation metrics must be strings."
    )
    mock_llmobs_logs.reset_mock()
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
def test_submit_evaluation_metric_tags(llmobs, mock_llmobs_eval_metric_writer):
    llmobs.submit_evaluation(
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


@pytest.mark.parametrize(
    "ddtrace_global_config",
    [dict(ddtrace="1.2.3", env="test_env", service="test_service", _llmobs_ml_app="test_app_name")],
)
def test_submit_evaluation_metric_with_metadata_enqueues_metric(llmobs, mock_llmobs_eval_metric_writer):
    llmobs.submit_evaluation(
        span_context={"span_id": "123", "trace_id": "456"},
        label="toxicity",
        metric_type="categorical",
        value="high",
        tags={"foo": "bar", "bee": "baz", "ml_app": "ml_app_override"},
        ml_app="ml_app_override",
        metadata={"foo": ["bar", "baz"]},
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
            metadata={"foo": ["bar", "baz"]},
        )
    )
    mock_llmobs_eval_metric_writer.reset()
    llmobs.submit_evaluation(
        span_context={"span_id": "123", "trace_id": "456"},
        label="toxicity",
        metric_type="categorical",
        value="high",
        tags={"foo": "bar", "bee": "baz", "ml_app": "ml_app_override"},
        ml_app="ml_app_override",
        metadata="invalid",
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


def test_submit_evaluation_enqueues_writer_with_categorical_metric(llmobs, mock_llmobs_eval_metric_writer):
    llmobs.submit_evaluation(
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
    with llmobs.llm(model_name="test_model", name="test_llm_call", model_provider="test_provider") as span:
        llmobs.submit_evaluation(
            span_context=llmobs.export_span(span),
            label="toxicity",
            metric_type="categorical",
            value="high",
            ml_app="dummy",
        )
    mock_llmobs_eval_metric_writer.enqueue.assert_called_with(
        _expected_llmobs_eval_metric_event(
            ml_app="dummy",
            span_id=str(span.span_id),
            trace_id=format_trace_id(span.trace_id),
            label="toxicity",
            metric_type="categorical",
            categorical_value="high",
        )
    )


def test_submit_evaluation_enqueues_writer_with_score_metric(llmobs, mock_llmobs_eval_metric_writer):
    llmobs.submit_evaluation(
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
    with llmobs.llm(model_name="test_model", name="test_llm_call", model_provider="test_provider") as span:
        llmobs.submit_evaluation(
            span_context=llmobs.export_span(span), label="sentiment", metric_type="score", value=0.9, ml_app="dummy"
        )
    mock_llmobs_eval_metric_writer.enqueue.assert_called_with(
        _expected_llmobs_eval_metric_event(
            span_id=str(span.span_id),
            trace_id=format_trace_id(span.trace_id),
            label="sentiment",
            metric_type="score",
            score_value=0.9,
            ml_app="dummy",
        )
    )


def test_submit_evaluation_with_numerical_metric_enqueues_writer_with_score_metric(
    llmobs, mock_llmobs_eval_metric_writer
):
    llmobs.submit_evaluation(
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
    with llmobs.llm(model_name="test_model", name="test_llm_call", model_provider="test_provider") as span:
        llmobs.submit_evaluation(
            span_context=llmobs.export_span(span),
            label="token_count",
            metric_type="numerical",
            value=35,
            ml_app="dummy",
        )
    mock_llmobs_eval_metric_writer.enqueue.assert_called_with(
        _expected_llmobs_eval_metric_event(
            ml_app="dummy",
            span_id=str(span.span_id),
            trace_id=format_trace_id(span.trace_id),
            label="token_count",
            metric_type="score",
            score_value=35,
        )
    )


def test_flush_does_not_call_periodic_when_llmobs_is_disabled(
    llmobs,
    mock_llmobs_eval_metric_writer,
    mock_llmobs_evaluator_runner,
    mock_llmobs_logs,
):
    llmobs.enabled = False
    llmobs.flush()
    mock_llmobs_eval_metric_writer.periodic.assert_not_called()
    mock_llmobs_evaluator_runner.periodic.assert_not_called()
    mock_llmobs_logs.warning.assert_has_calls(
        [mock.call("flushing when LLMObs is disabled. No spans or evaluation metrics will be sent.")]
    )
    llmobs.enabled = True


def test_inject_distributed_headers_llmobs_disabled_does_nothing(llmobs, mock_llmobs_logs):
    llmobs.disable()
    headers = llmobs.inject_distributed_headers({}, span=None)
    mock_llmobs_logs.warning.assert_called_once_with(
        "LLMObs.inject_distributed_headers() called when LLMObs is not enabled. "
        "Distributed context will not be injected."
    )
    assert headers == {}


def test_inject_distributed_headers_not_dict_logs_warning(llmobs, mock_llmobs_logs):
    headers = llmobs.inject_distributed_headers("not a dictionary", span=None)
    mock_llmobs_logs.warning.assert_called_once_with("request_headers must be a dictionary of string key-value pairs.")
    assert headers == "not a dictionary"
    mock_llmobs_logs.reset_mock()
    headers = llmobs.inject_distributed_headers(123, span=None)
    mock_llmobs_logs.warning.assert_called_once_with("request_headers must be a dictionary of string key-value pairs.")
    assert headers == 123
    mock_llmobs_logs.reset_mock()
    headers = llmobs.inject_distributed_headers(None, span=None)
    mock_llmobs_logs.warning.assert_called_once_with("request_headers must be a dictionary of string key-value pairs.")
    assert headers is None


def test_inject_distributed_headers_no_active_span_logs_warning(llmobs, mock_llmobs_logs):
    headers = llmobs.inject_distributed_headers({}, span=None)
    mock_llmobs_logs.warning.assert_called_once_with("No span provided and no currently active span found.")
    assert headers == {}


def test_inject_distributed_headers_span_calls_httppropagator_inject(llmobs, mock_llmobs_logs):
    span = llmobs._instance.tracer.trace("test_span")
    with mock.patch("ddtrace.propagation.http.HTTPPropagator.inject") as mock_inject:
        llmobs.inject_distributed_headers({}, span=span)
        assert mock_inject.call_count == 1
        mock_inject.assert_called_once_with(span.context, {})


def test_inject_distributed_headers_current_active_span_injected(llmobs, mock_llmobs_logs):
    span = llmobs.workflow("test_span")
    with mock.patch("ddtrace.llmobs._llmobs.HTTPPropagator.inject") as mock_inject:
        llmobs.inject_distributed_headers({}, span=None)
        assert mock_inject.call_count == 1
        mock_inject.assert_called_once_with(span.context, {})


def test_activate_distributed_headers_llmobs_disabled_does_nothing(llmobs, mock_llmobs_logs):
    llmobs.disable()
    llmobs.activate_distributed_headers({})
    mock_llmobs_logs.warning.assert_called_once_with(
        "LLMObs.activate_distributed_headers() called when LLMObs is not enabled. "
        "Distributed context will not be activated."
    )


def test_activate_distributed_headers_calls_httppropagator_extract(llmobs, mock_llmobs_logs):
    with mock.patch("ddtrace.llmobs._llmobs.HTTPPropagator.extract") as mock_extract:
        llmobs.activate_distributed_headers({})
        assert mock_extract.call_count == 1
        mock_extract.assert_called_once_with({})


def test_activate_distributed_headers_no_trace_id_does_nothing(llmobs, mock_llmobs_logs):
    with mock.patch("ddtrace.llmobs._llmobs.HTTPPropagator.extract") as mock_extract:
        mock_extract.return_value = Context(span_id=123)
        llmobs.activate_distributed_headers({})
        assert mock_extract.call_count == 1
        mock_llmobs_logs.warning.assert_called_once_with("Failed to extract trace/span ID from request headers.")


def test_activate_distributed_headers_no_span_id_does_nothing(llmobs, mock_llmobs_logs):
    with mock.patch("ddtrace.llmobs._llmobs.HTTPPropagator.extract") as mock_extract:
        mock_extract.return_value = Context(trace_id=123)
        llmobs.activate_distributed_headers({})
        assert mock_extract.call_count == 1
        mock_llmobs_logs.warning.assert_called_once_with("Failed to extract trace/span ID from request headers.")


def test_activate_distributed_headers_no_llmobs_parent_id_does_nothing(llmobs, mock_llmobs_logs):
    with mock.patch("ddtrace.llmobs._llmobs.HTTPPropagator.extract") as mock_extract:
        dummy_context = Context(trace_id=123, span_id=456)
        mock_extract.return_value = dummy_context
        with mock.patch("ddtrace.llmobs.LLMObs._instance.tracer.context_provider.activate") as mock_activate:
            llmobs.activate_distributed_headers({})
            assert mock_extract.call_count == 1
            mock_llmobs_logs.warning.assert_called_once_with("Failed to extract LLMObs parent ID from request headers.")
            mock_activate.assert_called_once_with(dummy_context)


def test_activate_distributed_headers_activates_context(llmobs, mock_llmobs_logs):
    with mock.patch("ddtrace.llmobs._llmobs.HTTPPropagator.extract") as mock_extract:
        dummy_context = Context(trace_id=123, span_id=456)
        mock_extract.return_value = dummy_context
        with mock.patch("ddtrace.llmobs.LLMObs._instance.tracer.context_provider.activate") as mock_activate:
            llmobs.activate_distributed_headers({})
            assert mock_extract.call_count == 1
            mock_activate.assert_called_once_with(dummy_context)


def test_listener_hooks_enqueue_correct_writer(run_python_code_in_subprocess):
    """
    Regression test that ensures that listener hooks enqueue span events to the correct writer,
    not the default writer created at startup.
    """
    env = os.environ.copy()
    pypath = [os.path.dirname(os.path.dirname(os.path.dirname(os.path.dirname(__file__))))]
    if "PYTHONPATH" in env:
        pypath.append(env["PYTHONPATH"])
    env.update({"PYTHONPATH": ":".join(pypath), "DD_TRACE_ENABLED": "0"})
    out, err, status, pid = run_python_code_in_subprocess(
        """
from ddtrace.llmobs import LLMObs

LLMObs.enable(ml_app="repro-issue", agentless_enabled=True, api_key="foobar.baz", site="datad0g.com")
with LLMObs.agent("dummy"):
    pass
""",
        env=env,
    )
    assert status == 0, err
    assert out == b""
    agentless_writer_log = b"failed to send traces to intake at https://llmobs-intake.datad0g.com/api/v2/llmobs: HTTP error status 403, reason Forbidden\n"  # noqa: E501
    agent_proxy_log = b"failed to send, dropping 1 traces to intake at http://localhost:8126/evp_proxy/v2/api/v2/llmobs after 5 retries"  # noqa: E501
    assert err == agentless_writer_log
    assert agent_proxy_log not in err


def test_llmobs_fork_recreates_and_restarts_span_writer():
    """Test that forking a process correctly recreates and restarts the LLMObsSpanWriter."""
    with mock.patch("ddtrace.internal.writer.HTTPWriter._send_payload"):
        llmobs_service.enable(_tracer=DummyTracer(), ml_app="test_app", agentless_enabled=False)
        original_span_writer = llmobs_service._instance._llmobs_span_writer
        pid = os.fork()
        if pid:  # parent
            assert llmobs_service._instance._llmobs_span_writer == original_span_writer
            assert llmobs_service._instance._llmobs_span_writer.status == ServiceStatus.RUNNING
        else:  # child
            assert llmobs_service._instance._llmobs_span_writer != original_span_writer
            assert llmobs_service._instance._llmobs_span_writer.status == ServiceStatus.RUNNING
            llmobs_service.disable()
            os._exit(12)

        _, status = os.waitpid(pid, 0)
        exit_code = os.WEXITSTATUS(status)
        assert exit_code == 12
        llmobs_service.disable()


def test_llmobs_fork_recreates_and_restarts_agentless_span_writer():
    """Test that forking a process correctly recreates and restarts the LLMObsSpanWriter."""
    with override_global_config(dict(_dd_api_key="<not-a-real-key>")):
        with mock.patch("ddtrace.internal.writer.HTTPWriter._send_payload"):
            llmobs_service.enable(_tracer=DummyTracer(), ml_app="test_app", agentless_enabled=True)
            original_span_writer = llmobs_service._instance._llmobs_span_writer
            pid = os.fork()
            if pid:  # parent
                assert llmobs_service._instance._llmobs_span_writer == original_span_writer
                assert llmobs_service._instance._llmobs_span_writer.status == ServiceStatus.RUNNING
            else:  # child
                assert llmobs_service._instance._llmobs_span_writer != original_span_writer
                assert llmobs_service._instance._llmobs_span_writer.status == ServiceStatus.RUNNING
                llmobs_service.disable()
                os._exit(12)

            _, status = os.waitpid(pid, 0)
            exit_code = os.WEXITSTATUS(status)
            assert exit_code == 12
            llmobs_service.disable()


def test_llmobs_fork_recreates_and_restarts_eval_metric_writer():
    """Test that forking a process correctly recreates and restarts the LLMObsEvalMetricWriter."""
    with mock.patch("ddtrace.llmobs._writer.BaseLLMObsWriter.periodic"):
        llmobs_service.enable(_tracer=DummyTracer(), ml_app="test_app")
        original_eval_metric_writer = llmobs_service._instance._llmobs_eval_metric_writer
        pid = os.fork()
        if pid:  # parent
            assert llmobs_service._instance._llmobs_eval_metric_writer == original_eval_metric_writer
            assert llmobs_service._instance._llmobs_eval_metric_writer.status == ServiceStatus.RUNNING
        else:  # child
            assert llmobs_service._instance._llmobs_eval_metric_writer != original_eval_metric_writer
            assert llmobs_service._instance._llmobs_eval_metric_writer.status == ServiceStatus.RUNNING
            llmobs_service.disable()
            os._exit(12)

        _, status = os.waitpid(pid, 0)
        exit_code = os.WEXITSTATUS(status)
        assert exit_code == 12
        llmobs_service.disable()


def test_llmobs_fork_recreates_and_restarts_evaluator_runner(mock_ragas_evaluator):
    """Test that forking a process correctly recreates and restarts the EvaluatorRunner."""
    pytest.importorskip("ragas")
    with override_env(dict(DD_LLMOBS_EVALUATORS="ragas_faithfulness")):
        with mock.patch("ddtrace.llmobs._evaluators.runner.EvaluatorRunner.periodic"):
            llmobs_service.enable(_tracer=DummyTracer(), ml_app="test_app")
            original_evaluator_runner = llmobs_service._instance._evaluator_runner
            pid = os.fork()
            if pid:  # parent
                assert llmobs_service._instance._evaluator_runner == original_evaluator_runner
                assert llmobs_service._instance._evaluator_runner.status == ServiceStatus.RUNNING
            else:  # child
                assert llmobs_service._instance._evaluator_runner != original_evaluator_runner
                assert llmobs_service._instance._evaluator_runner.status == ServiceStatus.RUNNING
                llmobs_service.disable()
                os._exit(12)

            _, status = os.waitpid(pid, 0)
            exit_code = os.WEXITSTATUS(status)
            assert exit_code == 12
            llmobs_service.disable()


def test_llmobs_fork_create_span(monkeypatch):
    """Test that forking a process correctly encodes new spans created in each process."""
    monkeypatch.setenv("_DD_LLMOBS_WRITER_INTERVAL", "5.0")
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


def test_llmobs_fork_submit_evaluation(monkeypatch):
    """Test that forking a process correctly encodes new spans created in each process."""
    monkeypatch.setenv("_DD_LLMOBS_WRITER_INTERVAL", 5.0)
    with mock.patch("ddtrace.llmobs._writer.BaseLLMObsWriter.periodic"):
        llmobs_service.enable(_tracer=DummyTracer(), ml_app="test_app", api_key="test_api_key")
        pid = os.fork()
        if pid:  # parent
            llmobs_service.submit_evaluation(
                span_context={"span_id": "123", "trace_id": "456"},
                label="toxicity",
                metric_type="categorical",
                value="high",
            )
            assert len(llmobs_service._instance._llmobs_eval_metric_writer._buffer) == 1
        else:  # child
            llmobs_service.submit_evaluation(
                span_context={"span_id": "123", "trace_id": "456"},
                label="toxicity",
                metric_type="categorical",
                value="high",
            )
            assert len(llmobs_service._instance._llmobs_eval_metric_writer._buffer) == 1
            llmobs_service.disable()
            os._exit(12)

        _, status = os.waitpid(pid, 0)
        exit_code = os.WEXITSTATUS(status)
        assert exit_code == 12
        llmobs_service.disable()


def test_llmobs_fork_evaluator_runner_run(monkeypatch):
    """Test that forking a process correctly encodes new spans created in each process."""
    monkeypatch.setenv("DD_LLMOBS_EVALUATOR_INTERVAL", 5.0)
    pytest.importorskip("ragas")
    monkeypatch.setenv("DD_LLMOBS_EVALUATORS", "ragas_faithfulness")
    with mock.patch("ddtrace.llmobs._evaluators.runner.EvaluatorRunner.periodic"):
        llmobs_service.enable(_tracer=DummyTracer(), ml_app="test_app", api_key="test_api_key")
        pid = os.fork()
        if pid:  # parent
            llmobs_service._instance._evaluator_runner.enqueue({"span_id": "123", "trace_id": "456"}, None)
            assert len(llmobs_service._instance._evaluator_runner._buffer) == 1
        else:  # child
            llmobs_service._instance._evaluator_runner.enqueue({"span_id": "123", "trace_id": "456"}, None)
            assert len(llmobs_service._instance._evaluator_runner._buffer) == 1
            llmobs_service.disable()
            os._exit(12)

        _, status = os.waitpid(pid, 0)
        exit_code = os.WEXITSTATUS(status)
        assert exit_code == 12
        llmobs_service.disable()


def test_llmobs_fork_disabled(monkeypatch):
    """Test that after being disabled the service remains disabled when forking"""
    monkeypatch.setenv("DD_LLMOBS_ENABLED", "0")
    svc = llmobs_service(tracer=DummyTracer())
    pid = os.fork()
    assert not svc.enabled, "both the parent and child should be disabled"
    assert svc._llmobs_span_writer.status == ServiceStatus.STOPPED
    assert svc._llmobs_eval_metric_writer.status == ServiceStatus.STOPPED
    if not pid:
        svc.disable()
        os._exit(12)

    _, status = os.waitpid(pid, 0)
    exit_code = os.WEXITSTATUS(status)
    assert exit_code == 12
    svc.disable()


def test_llmobs_fork_disabled_then_enabled(monkeypatch):
    """Test that after being initially disabled, the service can be enabled in a fork"""
    monkeypatch.setenv("DD_LLMOBS_ENABLED", "0")
    svc = llmobs_service._instance
    pid = os.fork()
    assert not svc.enabled, "both the parent and child should be disabled"
    assert svc._llmobs_span_writer.status == ServiceStatus.STOPPED
    assert svc._llmobs_eval_metric_writer.status == ServiceStatus.STOPPED
    if not pid:
        # Enable the service in the child
        with override_global_config(dict(_dd_api_key="<not-a-real-api-key>", _llmobs_ml_app="<ml-app-name>")):
            monkeypatch.setenv("DD_LLMOBS_ENABLED", "1")
            llmobs_service.enable(_tracer=DummyTracer())
        svc = llmobs_service._instance
        assert svc._llmobs_span_writer.status == ServiceStatus.RUNNING
        assert svc._llmobs_eval_metric_writer.status == ServiceStatus.RUNNING
        svc.disable()
        os._exit(12)

    _, status = os.waitpid(pid, 0)
    exit_code = os.WEXITSTATUS(status)
    assert exit_code == 12
    svc.disable()


def test_llmobs_with_evaluator_runner(llmobs, mock_llmobs_evaluator_runner):
    with llmobs.llm(model_name="test_model"):
        pass
    time.sleep(0.1)
    assert llmobs._instance._evaluator_runner.enqueue.call_count == 1


def test_llmobs_with_evaluator_runner_does_not_enqueue_evaluation_spans(mock_llmobs_evaluator_runner, llmobs):
    with llmobs.agent(name="test") as agent:
        agent._set_ctx_item(IS_EVALUATION_SPAN, True)
        with llmobs.llm(model_name="test_model"):
            pass
    time.sleep(0.1)
    assert llmobs._instance._evaluator_runner.enqueue.call_count == 0


def test_llmobs_with_evaluation_runner_does_not_enqueue_non_llm_spans(mock_llmobs_evaluator_runner, llmobs):
    with llmobs.workflow(name="test"):
        pass
    with llmobs.agent(name="test"):
        pass
    with llmobs.task(name="test"):
        pass
    with llmobs.embedding(model_name="test"):
        pass
    with llmobs.retrieval(name="test"):
        pass
    with llmobs.tool(name="test"):
        pass
    time.sleep(0.1)
    assert llmobs._instance._evaluator_runner.enqueue.call_count == 0


def test_annotation_context_modifies_span_tags(llmobs):
    with llmobs.annotation_context(tags={"foo": "bar"}):
        with llmobs.agent(name="test_agent") as span:
            assert span._get_ctx_item(TAGS) == {"foo": "bar"}


def test_annotation_context_modifies_prompt(llmobs):
    with llmobs.annotation_context(prompt={"template": "test_template"}):
        with llmobs.llm(name="test_agent", model_name="test") as span:
            assert span._get_ctx_item(INPUT_PROMPT) == {
                "template": "test_template",
                "_dd_context_variable_keys": ["context"],
                "_dd_query_variable_keys": ["question"],
            }


def test_annotation_context_modifies_name(llmobs):
    with llmobs.annotation_context(name="test_agent_override"):
        with llmobs.llm(name="test_agent", model_name="test") as span:
            assert span.name == "test_agent_override"


def test_annotation_context_finished_context_does_not_modify_tags(llmobs):
    with llmobs.annotation_context(tags={"foo": "bar"}):
        pass
    with llmobs.agent(name="test_agent") as span:
        assert span._get_ctx_item(TAGS) is None


def test_annotation_context_finished_context_does_not_modify_prompt(llmobs):
    with llmobs.annotation_context(prompt={"template": "test_template"}):
        pass
    with llmobs.llm(name="test_agent", model_name="test") as span:
        assert span._get_ctx_item(INPUT_PROMPT) is None


def test_annotation_context_finished_context_does_not_modify_name(llmobs):
    with llmobs.annotation_context(name="test_agent_override"):
        pass
    with llmobs.agent(name="test_agent") as span:
        assert span.name == "test_agent"


def test_annotation_context_nested(llmobs):
    with llmobs.annotation_context(tags={"foo": "bar", "boo": "bar"}):
        with llmobs.annotation_context(tags={"foo": "baz"}):
            with llmobs.agent(name="test_agent") as span:
                assert span._get_ctx_item(TAGS) == {"foo": "baz", "boo": "bar"}


def test_annotation_context_nested_overrides_name(llmobs):
    with llmobs.annotation_context(name="unexpected"):
        with llmobs.annotation_context(name="expected"):
            with llmobs.agent(name="test_agent") as span:
                assert span.name == "expected"


def test_annotation_context_nested_maintains_trace_structure(llmobs, llmobs_events):
    """This test makes sure starting/stopping annotation contexts do not modify the llmobs trace structure"""
    with llmobs.annotation_context(tags={"foo": "bar", "boo": "bar"}):
        with llmobs.agent(name="parent_span") as parent_span:
            with llmobs.annotation_context(tags={"foo": "baz"}):
                with llmobs.workflow(name="child_span") as child_span:
                    assert child_span._get_ctx_item(TAGS) == {"foo": "baz", "boo": "bar"}
                    assert parent_span._get_ctx_item(TAGS) == {"foo": "bar", "boo": "bar"}

    assert len(llmobs_events) == 2
    parent_span, child_span = llmobs_events[1], llmobs_events[0]
    assert child_span["trace_id"] == parent_span["trace_id"]
    assert child_span["span_id"] != parent_span["span_id"]
    assert child_span["parent_id"] == parent_span["span_id"]
    assert parent_span["parent_id"] == "undefined"


def test_annotation_context_separate_traces_maintained(llmobs, llmobs_events):
    with llmobs.annotation_context(tags={"foo": "bar", "boo": "bar"}):
        with llmobs.agent(name="parent_span"):
            pass
        with llmobs.workflow(name="child_span"):
            pass

    assert len(llmobs_events) == 2
    agent_span, workflow_span = llmobs_events[1], llmobs_events[0]
    assert agent_span["trace_id"] != workflow_span["trace_id"]
    assert agent_span["span_id"] != workflow_span["span_id"]
    assert workflow_span["parent_id"] == "undefined"
    assert agent_span["parent_id"] == "undefined"


def test_annotation_context_only_applies_to_local_context(llmobs):
    """
    tests that annotation contexts only apply to spans belonging to the same
    trace context and not globally to all spans.
    """
    agent_has_correct_name = False
    agent_has_correct_tags = False
    tool_has_correct_name = False
    tool_does_not_have_tags = False

    event = threading.Event()

    # thread which registers an annotation context for 0.1 seconds
    def context_one():
        nonlocal agent_has_correct_name
        nonlocal agent_has_correct_tags
        with llmobs.annotation_context(name="expected_agent", tags={"foo": "bar"}):
            with llmobs.agent(name="test_agent") as span:
                event.wait()
                agent_has_correct_tags = span._get_ctx_item(TAGS) == {"foo": "bar"}
                agent_has_correct_name = span.name == "expected_agent"

    # thread which registers an annotation context for 0.5 seconds
    def context_two():
        nonlocal tool_has_correct_name
        nonlocal tool_does_not_have_tags
        with llmobs.agent(name="test_agent"):
            with llmobs.annotation_context(name="expected_tool"):
                with llmobs.tool(name="test_tool") as tool_span:
                    event.wait()
                    tool_does_not_have_tags = tool_span._get_ctx_item(TAGS) is None
                    tool_has_correct_name = tool_span.name == "expected_tool"

    thread_one = threading.Thread(target=context_one)
    thread_two = threading.Thread(target=context_two)
    thread_one.start()
    thread_two.start()

    with llmobs.agent(name="test_agent") as span:
        assert span.name == "test_agent"
        assert span._get_ctx_item(TAGS) is None

    event.set()
    thread_one.join()
    thread_two.join()

    # the context's in each thread shouldn't alter the span name of
    # spans started in other threads.
    assert agent_has_correct_name is True
    assert tool_has_correct_name is True
    assert agent_has_correct_tags is True
    assert tool_does_not_have_tags is True


async def test_annotation_context_async_modifies_span_tags(llmobs):
    async with llmobs.annotation_context(tags={"foo": "bar"}):
        with llmobs.agent(name="test_agent") as span:
            assert span._get_ctx_item(TAGS) == {"foo": "bar"}


async def test_annotation_context_async_modifies_prompt(llmobs):
    async with llmobs.annotation_context(prompt={"template": "test_template"}):
        with llmobs.llm(name="test_agent", model_name="test") as span:
            assert span._get_ctx_item(INPUT_PROMPT) == {
                "template": "test_template",
                "_dd_context_variable_keys": ["context"],
                "_dd_query_variable_keys": ["question"],
            }


async def test_annotation_context_async_modifies_name(llmobs):
    async with llmobs.annotation_context(name="test_agent_override"):
        with llmobs.llm(name="test_agent", model_name="test") as span:
            assert span.name == "test_agent_override"


async def test_annotation_context_async_finished_context_does_not_modify_tags(llmobs):
    async with llmobs.annotation_context(tags={"foo": "bar"}):
        pass
    with llmobs.agent(name="test_agent") as span:
        assert span._get_ctx_item(TAGS) is None


async def test_annotation_context_async_finished_context_does_not_modify_prompt(llmobs):
    async with llmobs.annotation_context(prompt={"template": "test_template"}):
        pass
    with llmobs.llm(name="test_agent", model_name="test") as span:
        assert span._get_ctx_item(INPUT_PROMPT) is None


async def test_annotation_context_finished_context_async_does_not_modify_name(llmobs):
    async with llmobs.annotation_context(name="test_agent_override"):
        pass
    with llmobs.agent(name="test_agent") as span:
        assert span.name == "test_agent"


async def test_annotation_context_async_nested(llmobs):
    async with llmobs.annotation_context(tags={"foo": "bar", "boo": "bar"}):
        async with llmobs.annotation_context(tags={"foo": "baz"}):
            with llmobs.agent(name="test_agent") as span:
                assert span._get_ctx_item(TAGS) == {"foo": "baz", "boo": "bar"}


def test_service_enable_starts_evaluator_runner_when_evaluators_exist():
    pytest.importorskip("ragas")
    with override_global_config(dict(_dd_api_key="<not-a-real-api-key>", _llmobs_ml_app="<ml-app-name>")):
        with override_env(dict(DD_LLMOBS_EVALUATORS="ragas_faithfulness")):
            dummy_tracer = DummyTracer()
            llmobs_service.enable(_tracer=dummy_tracer)
            llmobs_instance = llmobs_service._instance
            assert llmobs_instance is not None
            assert llmobs_service.enabled
            assert llmobs_service._instance._llmobs_eval_metric_writer.status.value == "running"
            assert llmobs_service._instance._evaluator_runner.status.value == "running"
            llmobs_service.disable()


def test_service_enable_does_not_start_evaluator_runner():
    with override_global_config(dict(_dd_api_key="<not-a-real-api-key>", _llmobs_ml_app="<ml-app-name>")):
        dummy_tracer = DummyTracer()
        llmobs_service.enable(_tracer=dummy_tracer)
        llmobs_instance = llmobs_service._instance
        assert llmobs_instance is not None
        assert llmobs_service.enabled
        assert llmobs_service._instance._llmobs_eval_metric_writer.status.value == "running"
        assert llmobs_service._instance._llmobs_span_writer.status.value == "running"
        assert llmobs_service._instance._evaluator_runner.status.value == "stopped"
        llmobs_service.disable()


def test_submit_evaluation_llmobs_disabled_raises_debug(llmobs, mock_llmobs_logs):
    llmobs.disable()
    mock_llmobs_logs.reset_mock()
    llmobs.submit_evaluation(
        span_context={"span_id": "123", "trace_id": "456"}, label="toxicity", metric_type="categorical", value="high"
    )
    mock_llmobs_logs.debug.assert_called_once_with(
        "LLMObs.submit_evaluation() called when LLMObs is not enabled. Evaluation metric data will not be sent."
    )


def test_submit_evaluation_for_invalid_metadata_raises_warning(llmobs, mock_llmobs_logs):
    llmobs.submit_evaluation(
        span_context={"span_id": "123", "trace_id": "456"},
        label="toxicity",
        metric_type="categorical",
        value="high",
        metadata=1,
    )
    mock_llmobs_logs.warning.assert_called_once_with("metadata must be json serializable dictionary.")


def test_submit_evaluation_for_no_ml_app_raises_warning(llmobs, mock_llmobs_logs):
    with override_global_config(dict(_llmobs_ml_app="")):
        llmobs.submit_evaluation_for(
            span={"span_id": "123", "trace_id": "456"},
            label="toxicity",
            metric_type="categorical",
            value="high",
        )
        mock_llmobs_logs.warning.assert_called_once_with(
            "ML App name is required for sending evaluation metrics. Evaluation metric data will not be sent. "
            "Ensure this configuration is set before running your application."
        )


def test_submit_evaluation_for_span_incorrect_type_raises_error(llmobs, mock_llmobs_logs):
    with pytest.raises(
        TypeError,
        match=re.escape(
            (
                "`span` must be a dictionary containing both span_id and trace_id keys. "
                "LLMObs.export_span() can be used to generate this dictionary from a given span."
            )
        ),
    ):
        llmobs.submit_evaluation_for(span="asd", label="toxicity", metric_type="categorical", value="high")


def test_submit_evaluation_for_span_with_tag_value_incorrect_type_raises_error(llmobs, mock_llmobs_logs):
    with pytest.raises(
        TypeError,
        match=r"`span_with_tag_value` must be a dict with keys 'tag_key' and 'tag_value' containing string values",
    ):
        llmobs.submit_evaluation_for(
            span_with_tag_value="asd", label="toxicity", metric_type="categorical", value="high"
        )
    with pytest.raises(
        TypeError,
        match=r"`span_with_tag_value` must be a dict with keys 'tag_key' and 'tag_value' containing string values",
    ):
        llmobs.submit_evaluation_for(
            span_with_tag_value={"tag_key": "hi", "tag_value": 1},
            label="toxicity",
            metric_type="categorical",
            value="high",
        )


def test_submit_evaluation_for_empty_span_or_trace_id_raises_error(llmobs, mock_llmobs_logs):
    with pytest.raises(
        TypeError,
        match=re.escape(
            (
                "`span` must be a dictionary containing both span_id and trace_id keys. "
                "LLMObs.export_span() can be used to generate this dictionary from a given span."
            )
        ),
    ):
        llmobs.submit_evaluation_for(
            span={"trace_id": "456"}, label="toxicity", metric_type="categorical", value="high"
        )
    with pytest.raises(
        TypeError,
        match=re.escape(
            "`span` must be a dictionary containing both span_id and trace_id keys. "
            "LLMObs.export_span() can be used to generate this dictionary from a given span."
        ),
    ):
        llmobs.submit_evaluation_for(span={"span_id": "456"}, label="toxicity", metric_type="categorical", value="high")


def test_submit_evaluation_for_span_with_tag_value_empty_key_or_val_raises_error(llmobs, mock_llmobs_logs):
    with pytest.raises(
        TypeError,
        match=r"`span_with_tag_value` must be a dict with keys 'tag_key' and 'tag_value' containing string values",
    ):
        llmobs.submit_evaluation_for(
            span_with_tag_value={"tag_value": "123"}, label="toxicity", metric_type="categorical", value="high"
        )


def test_submit_evaluation_for_invalid_timestamp_raises_error(llmobs, mock_llmobs_logs):
    with pytest.raises(
        ValueError, match="timestamp_ms must be a non-negative integer. Evaluation metric data will not be sent"
    ):
        llmobs.submit_evaluation_for(
            span={"span_id": "123", "trace_id": "456"},
            label="",
            metric_type="categorical",
            value="high",
            ml_app="dummy",
            timestamp_ms="invalid",
        )


def test_submit_evaluation_for_empty_label_raises_error(llmobs, mock_llmobs_logs):
    with pytest.raises(ValueError, match="label must be the specified name of the evaluation metric."):
        llmobs.submit_evaluation_for(
            span={"span_id": "123", "trace_id": "456"}, label="", metric_type="categorical", value="high"
        )


def test_submit_evaluation_for_incorrect_metric_type_raises_error(llmobs, mock_llmobs_logs):
    with pytest.raises(ValueError, match="metric_type must be one of 'categorical' or 'score'."):
        llmobs.submit_evaluation_for(
            span={"span_id": "123", "trace_id": "456"}, label="toxicity", metric_type="wrong", value="high"
        )
    with pytest.raises(ValueError, match="metric_type must be one of 'categorical' or 'score'."):
        llmobs.submit_evaluation_for(
            span={"span_id": "123", "trace_id": "456"}, label="toxicity", metric_type="", value="high"
        )


def test_submit_evaluation_for_incorrect_score_value_type_raises_error(llmobs, mock_llmobs_logs):
    with pytest.raises(TypeError, match="value must be an integer or float for a score metric."):
        llmobs.submit_evaluation_for(
            span={"span_id": "123", "trace_id": "456"}, label="token_count", metric_type="score", value="high"
        )


def test_submit_evaluation_for_invalid_tags_raises_warning(llmobs, mock_llmobs_logs):
    llmobs.submit_evaluation_for(
        span={"span_id": "123", "trace_id": "456"},
        label="toxicity",
        metric_type="categorical",
        value="high",
        tags=["invalid"],
    )
    mock_llmobs_logs.warning.assert_called_once_with("tags must be a dictionary of string key-value pairs.")


@pytest.mark.parametrize(
    "ddtrace_global_config",
    [dict(_llmobs_ml_app="test_app_name")],
)
def test_submit_evaluation_for_non_string_tags_raises_warning_but_still_submits(
    llmobs, mock_llmobs_logs, mock_llmobs_eval_metric_writer
):
    llmobs.submit_evaluation_for(
        span={"span_id": "123", "trace_id": "456"},
        label="toxicity",
        metric_type="categorical",
        value="high",
        tags={1: 2, "foo": "bar"},
        ml_app="dummy",
    )
    mock_llmobs_logs.warning.assert_called_once_with(
        "Failed to parse tags. Tags for evaluation metrics must be strings."
    )
    mock_llmobs_logs.reset_mock()
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
def test_submit_evaluation_for_metric_tags(llmobs, mock_llmobs_eval_metric_writer):
    llmobs.submit_evaluation_for(
        span={"span_id": "123", "trace_id": "456"},
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


def test_submit_evaluation_for_span_with_tag_value_enqueues_writer_with_categorical_metric(
    llmobs, mock_llmobs_eval_metric_writer
):
    llmobs.submit_evaluation_for(
        span_with_tag_value={"tag_key": "tag_key", "tag_value": "tag_val"},
        label="toxicity",
        metric_type="categorical",
        value="high",
        ml_app="dummy",
    )
    mock_llmobs_eval_metric_writer.enqueue.assert_called_with(
        _expected_llmobs_eval_metric_event(
            ml_app="dummy",
            tag_key="tag_key",
            tag_value="tag_val",
            label="toxicity",
            metric_type="categorical",
            categorical_value="high",
        )
    )


def test_submit_evaluation_for_enqueues_writer_with_categorical_metric(llmobs, mock_llmobs_eval_metric_writer):
    llmobs.submit_evaluation_for(
        span={"span_id": "123", "trace_id": "456"},
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
    with llmobs.llm(model_name="test_model", name="test_llm_call", model_provider="test_provider") as span:
        llmobs.submit_evaluation_for(
            span=llmobs.export_span(span),
            label="toxicity",
            metric_type="categorical",
            value="high",
            ml_app="dummy",
        )
    mock_llmobs_eval_metric_writer.enqueue.assert_called_with(
        _expected_llmobs_eval_metric_event(
            ml_app="dummy",
            span_id=str(span.span_id),
            trace_id=format_trace_id(span.trace_id),
            label="toxicity",
            metric_type="categorical",
            categorical_value="high",
        )
    )


def test_submit_evaluation_for_enqueues_writer_with_score_metric(llmobs, mock_llmobs_eval_metric_writer):
    llmobs.submit_evaluation_for(
        span={"span_id": "123", "trace_id": "456"},
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
    with llmobs.llm(model_name="test_model", name="test_llm_call", model_provider="test_provider") as span:
        llmobs.submit_evaluation_for(
            span=llmobs.export_span(span), label="sentiment", metric_type="score", value=0.9, ml_app="dummy"
        )
    mock_llmobs_eval_metric_writer.enqueue.assert_called_with(
        _expected_llmobs_eval_metric_event(
            span_id=str(span.span_id),
            trace_id=format_trace_id(span.trace_id),
            label="sentiment",
            metric_type="score",
            score_value=0.9,
            ml_app="dummy",
        )
    )


def test_submit_evaluation_for_metric_with_metadata_enqueues_metric(llmobs, mock_llmobs_eval_metric_writer):
    llmobs.submit_evaluation_for(
        span={"span_id": "123", "trace_id": "456"},
        label="toxicity",
        metric_type="categorical",
        value="high",
        tags={"foo": "bar", "bee": "baz", "ml_app": "ml_app_override"},
        ml_app="ml_app_override",
        metadata={"foo": ["bar", "baz"]},
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
            metadata={"foo": ["bar", "baz"]},
        )
    )
    mock_llmobs_eval_metric_writer.reset()
    llmobs.submit_evaluation_for(
        span={"span_id": "123", "trace_id": "456"},
        label="toxicity",
        metric_type="categorical",
        value="high",
        tags={"foo": "bar", "bee": "baz", "ml_app": "ml_app_override"},
        ml_app="ml_app_override",
        metadata="invalid",
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


def test_llmobs_parenting_with_root_apm_span(llmobs, tracer, llmobs_events):
    # orphaned llmobs spans with apm root have undefined parent_id
    with tracer.trace("no_llm_span"):
        with llmobs.task("llm_span"):
            pass
        with llmobs.task("llm_span_2"):
            pass
    assert len(llmobs_events) == 2
    assert llmobs_events[0]["name"] == "llm_span"
    assert llmobs_events[0]["parent_id"] == "undefined"
    assert llmobs_events[1]["name"] == "llm_span_2"
    assert llmobs_events[1]["parent_id"] == "undefined"
    # document buggy `trace_id` behavior
    assert llmobs_events[0]["trace_id"] == llmobs_events[1]["trace_id"]


def test_llmobs_parenting_with_intermixed_apm_spans(llmobs, tracer, llmobs_events):
    with llmobs.task("level_1_llm"):
        with tracer.trace("intermediate_apm"):  # APM span
            with tracer.trace("intermediate_apm_2"):  # APM span
                with llmobs.task("level_2_llm_a"):
                    with tracer.trace("intermediate_apm_3"):  # APM span
                        with llmobs.task("level_3_llm"):
                            pass
                with llmobs.task("level_2_llm_b"):
                    pass
    """
    Expected LLM Obs trace structure;
        level_1_llm
            level_2_llm_a
                level_3_llm
            level_2_llm_b
    """
    assert len(llmobs_events) == 4
    assert llmobs_events[0]["name"] == "level_3_llm"
    assert llmobs_events[0]["parent_id"] == llmobs_events[1]["span_id"]

    assert llmobs_events[1]["name"] == "level_2_llm_a"
    assert llmobs_events[1]["parent_id"] == llmobs_events[3]["span_id"]

    assert llmobs_events[2]["name"] == "level_2_llm_b"
    assert llmobs_events[2]["parent_id"] == llmobs_events[3]["span_id"]

    assert llmobs_events[3]["name"] == "level_1_llm"
    assert llmobs_events[3]["parent_id"] == "undefined"
