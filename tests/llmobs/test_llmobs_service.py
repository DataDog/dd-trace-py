import json

import mock
import pytest

from ddtrace.llmobs import LLMObs as llmobs_service
from ddtrace.llmobs._llmobs import LLMObsTraceProcessor
from ddtrace.llmobs._constants import SESSION_ID
from ddtrace.llmobs._constants import SPAN_KIND
from ddtrace.llmobs._constants import TAGS
from ddtrace.llmobs._constants import MODEL_NAME
from ddtrace.llmobs._constants import MODEL_PROVIDER
from ddtrace.llmobs._constants import INPUT_VALUE
from ddtrace.llmobs._constants import INPUT_PARAMETERS
from ddtrace.llmobs._constants import INPUT_MESSAGES
from ddtrace.llmobs._constants import OUTPUT_VALUE
from ddtrace.llmobs._constants import OUTPUT_MESSAGES
from tests.utils import DummyTracer
from tests.utils import override_global_config


@pytest.fixture
def mock_logs():
    with mock.patch("ddtrace.llmobs._llmobs.log") as mock_logs:
        yield mock_logs


@pytest.fixture
def mock_llmobs_writer():
    patcher = mock.patch("ddtrace.llmobs._llmobs.LLMObsWriter")
    LLMObsWriterMock = patcher.start()
    m = mock.MagicMock()
    LLMObsWriterMock.return_value = m
    yield m
    patcher.stop()


@pytest.fixture
def LLMObs(mock_llmobs_writer):
    dummy_tracer = DummyTracer()
    llmobs_service.enable(tracer=dummy_tracer)
    yield llmobs_service
    llmobs_service.disable()


def test_llmobs_service_enable():
    with override_global_config(dict(_dd_api_key="<not-a-real-api-key>")):
        dummy_tracer = DummyTracer()
        llmobs_service.enable(tracer=dummy_tracer)
        llmobs_instance = llmobs_service._instance
        assert llmobs_instance is not None
        assert llmobs_service.enabled
        assert llmobs_instance.tracer == dummy_tracer
        assert any(isinstance(tracer_filter, LLMObsTraceProcessor) for tracer_filter in dummy_tracer._filters)
        llmobs_service.disable()


def test_llmobs_service_disable():
    with override_global_config(dict(_dd_api_key="<not-a-real-api-key>")):
        dummy_tracer = DummyTracer()
        llmobs_service.enable(tracer=dummy_tracer)
        llmobs_service.disable()
        assert llmobs_service._instance is None
        assert llmobs_service.enabled is False


def test_llmobs_service_enable_no_api_key():
    with override_global_config(dict(_dd_api_key="")):
        dummy_tracer = DummyTracer()
        with pytest.raises(ValueError):
            llmobs_service.enable(tracer=dummy_tracer)
        llmobs_instance = llmobs_service._instance
        assert llmobs_instance is None
        assert llmobs_service.enabled is False


def test_llmobs_service_enable_already_enabled(mock_logs):
    with override_global_config(dict(_dd_api_key="<not-a-real-api-key>")):
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


def test_llmobs_start_span_while_disabled_raises_error(LLMObs):
    LLMObs.disable()
    with pytest.raises(Exception, match="cannot be used while LLMObs is disabled."):
        with LLMObs.llm(model_name="test_model", name="test_llm_call", model_provider="test_provider"):
            pass
    with pytest.raises(Exception, match="cannot be used while LLMObs is disabled."):
        with LLMObs.tool(name="test_tool"):
            pass
    with pytest.raises(Exception, match="cannot be used while LLMObs is disabled."):
        with LLMObs.task(name="test_task"):
            pass
    with pytest.raises(Exception, match="cannot be used while LLMObs is disabled."):
        with LLMObs.workflow(name="test_workflow"):
            pass
    with pytest.raises(Exception, match="cannot be used while LLMObs is disabled."):
        with LLMObs.agent(name="test_agent"):
            pass


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
        {
            "trace_id": "{:x}".format(span.trace_id),
            "span_id": str(span.span_id),
            "parent_id": "",
            "session_id": session_id,
            "name": span.name,
            "tags": mock.ANY,
            "start_ns": span.start_ns,
            "duration": span.duration_ns,
            "error": 0,
            "meta": {"span.kind": "task"},
            "metrics": mock.ANY,
        },
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
        {
            "trace_id": "{:x}".format(span.trace_id),
            "span_id": str(span.span_id),
            "parent_id": "",
            "session_id": "{:x}".format(span.trace_id),
            "name": span.name,
            "tags": mock.ANY,
            "start_ns": span.start_ns,
            "duration": span.duration_ns,
            "error": 0,
            "meta": {"span.kind": "llm", "model_name": "test_model", "model_provider": "test_provider"},
            "metrics": mock.ANY,
        },
    )


def test_llmobs_llm_span_no_model_raises_error(LLMObs):
    with pytest.raises(TypeError):
        with LLMObs.llm(name="test_llm_call", model_provider="test_provider"):
            pass
    with pytest.raises(ValueError, match="model_name must be the specified name of the invoked model."):
        with LLMObs.llm(model_name="", name="test_llm_call", model_provider="test_provider"):
            pass


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
    mock_llmobs_writer.enqueue.assert_called_with(
        {
            "span_id": str(span.span_id),
            "trace_id": "{:x}".format(span.trace_id),
            "parent_id": "",
            "session_id": "{:x}".format(span.trace_id),
            "name": span.name,
            "tags": mock.ANY,
            "start_ns": span.start_ns,
            "duration": span.duration_ns,
            "error": 0,
            "meta": {"span.kind": "tool"},
            "metrics": mock.ANY,
        },
    )


def test_llmobs_task_span(LLMObs, mock_llmobs_writer):
    with LLMObs.task(name="test_task") as span:
        assert span.name == "test_task"
        assert span.resource == "task"
        assert span.span_type == "llm"
        assert span.get_tag(SPAN_KIND) == "task"
        assert span.get_tag(SESSION_ID) is None
    mock_llmobs_writer.enqueue.assert_called_with(
        {
            "span_id": str(span.span_id),
            "trace_id": "{:x}".format(span.trace_id),
            "parent_id": "",
            "session_id": "{:x}".format(span.trace_id),
            "name": span.name,
            "tags": mock.ANY,
            "start_ns": span.start_ns,
            "duration": span.duration_ns,
            "error": 0,
            "meta": {"span.kind": "task"},
            "metrics": mock.ANY,
        },
    )


def test_llmobs_workflow_span(LLMObs, mock_llmobs_writer):
    with LLMObs.workflow(name="test_workflow") as span:
        assert span.name == "test_workflow"
        assert span.resource == "workflow"
        assert span.span_type == "llm"
        assert span.get_tag(SPAN_KIND) == "workflow"
        assert span.get_tag(SESSION_ID) is None
    mock_llmobs_writer.enqueue.assert_called_with(
        {
            "span_id": str(span.span_id),
            "trace_id": "{:x}".format(span.trace_id),
            "parent_id": "",
            "session_id": "{:x}".format(span.trace_id),
            "name": span.name,
            "tags": mock.ANY,
            "start_ns": span.start_ns,
            "duration": span.duration_ns,
            "error": 0,
            "meta": {"span.kind": "workflow"},
            "metrics": mock.ANY,
        },
    )


def test_llmobs_agent_span(LLMObs, mock_llmobs_writer):
    with LLMObs.agent(name="test_agent") as span:
        assert span.name == "test_agent"
        assert span.resource == "agent"
        assert span.span_type == "llm"
        assert span.get_tag(SPAN_KIND) == "agent"
        assert span.get_tag(SESSION_ID) is None
    mock_llmobs_writer.enqueue.assert_called_with(
        {
            "span_id": str(span.span_id),
            "trace_id": "{:x}".format(span.trace_id),
            "parent_id": "",
            "session_id": "{:x}".format(span.trace_id),
            "name": span.name,
            "tags": mock.ANY,
            "start_ns": span.start_ns,
            "duration": span.duration_ns,
            "error": 0,
            "meta": {"span.kind": "agent"},
            "metrics": mock.ANY,
        },
    )


def test_llmobs_annotate_while_disabled_raises_error(LLMObs):
    LLMObs.disable()
    with pytest.raises(Exception, match="cannot be used while LLMObs is disabled."):
        LLMObs.annotate(parameters={"test": "test"})


def test_llmobs_annotate_no_active_span_raises_error(LLMObs):
    with pytest.raises(Exception, match="No span provided and no active span found."):
        LLMObs.annotate(parameters={"test": "test"})


def test_llmobs_annotate_non_llm_span_raises_error(LLMObs):
    dummy_tracer = DummyTracer()
    with dummy_tracer.trace("root") as non_llmobs_span:
        with pytest.raises(ValueError, match="span must be an LLM-type span."):
            LLMObs.annotate(span=non_llmobs_span, parameters={"test": "test"})


def test_llmobs_annotate_finished_span_does_nothing(LLMObs, mock_logs):
    with LLMObs.llm(model_name="test_model", name="test_llm_call", model_provider="test_provider") as span:
        pass
    LLMObs.annotate(span=span, parameters={"test": "test"})
    mock_logs.warning.assert_called_once_with("Cannot annotate a finished span.")


def test_llmobs_annotate_parameters(LLMObs):
    with LLMObs.llm(model_name="test_model", name="test_llm_call", model_provider="test_provider") as span:
        LLMObs.annotate(span=span, parameters={"temperature": 0.9, "max_tokens": 50})
        assert json.loads(span.get_tag(INPUT_PARAMETERS)) == {"temperature": 0.9, "max_tokens": 50}
        LLMObs.annotate(span=span, parameters={"n": 1})
        assert json.loads(span.get_tag(INPUT_PARAMETERS)) == {"temperature": 0.9, "max_tokens": 50, "n": 1}


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
        assert agent_span.get_tag(INPUT_VALUE) == "test_input"


def test_llmobs_annotate_input_llm_message(LLMObs):
    with LLMObs.llm(model_name="test_model") as llm_span:
        LLMObs.annotate(span=llm_span, input_data=[{"content": "test_input", "role": "human"}])
        assert json.loads(llm_span.get_tag(INPUT_MESSAGES)) == [{"content": "test_input", "role": "human"}]


def test_llmobs_annotate_non_llm_span_message_input_raises_error(LLMObs):
    with LLMObs.task() as span:
        with pytest.raises(ValueError, match="Invalid input/output type for non-llm span. Must be a raw string."):
            LLMObs.annotate(span=span, input_data=[{"content": "test_input"}])


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
        assert agent_span.get_tag(OUTPUT_VALUE) == "test_output"


def test_llmobs_annotate_output_llm_message(LLMObs):
    with LLMObs.llm(model_name="test_model") as llm_span:
        LLMObs.annotate(span=llm_span, output_data=[{"content": "test_output", "role": "human"}])
        assert json.loads(llm_span.get_tag(OUTPUT_MESSAGES)) == [{"content": "test_output", "role": "human"}]


def test_llmobs_annotate_non_llm_span_message_output_raises_error(LLMObs):
    with LLMObs.task() as span:
        with pytest.raises(ValueError, match="Invalid input/output type for non-llm span. Must be a raw string."):
            LLMObs.annotate(span=span, output_data=[{"content": "test_input"}])


def test_llmobs_span_error_sets_error_tag(LLMObs, mock_llmobs_writer):
    with pytest.raises(ValueError):
        with LLMObs.llm(model_name="test_model", model_provider="test_model_provider") as span:
            raise ValueError("test error message")
    mock_llmobs_writer.enqueue.assert_called_with(
        {
            "trace_id": "{:x}".format(span.trace_id),
            "span_id": str(span.span_id),
            "parent_id": "",
            "session_id": "{:x}".format(span.trace_id),
            "name": span.name,
            "tags": mock.ANY,
            "start_ns": span.start_ns,
            "duration": span.duration_ns,
            "error": 1,
            "meta": {
                "span.kind": "llm",
                "model_name": "test_model",
                "model_provider": "test_model_provider",
                "error.message": "test error message",
            },
            "metrics": mock.ANY,
        },
    )
