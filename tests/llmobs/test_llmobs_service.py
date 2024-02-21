import json

import mock
import pytest

from ddtrace.llmobs import LLMObs as llmobs_service
from ddtrace.llmobs._llmobs import LLMObsTraceProcessor
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
        with LLMObs.llm(model="test_model", name="test_llm_call", model_provider="test_provider"):
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
    with LLMObs.llm(model="test_model", model_provider="test_provider") as span:
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
    with LLMObs.llm(model="test_model", session_id="test_session_id") as span:
        span_meta = json.loads(span.get_tag("ml_obs.meta") or "{}")
        assert span_meta["session_id"] == "test_session_id"
    with LLMObs.tool(session_id="test_session_id") as span:
        span_meta = json.loads(span.get_tag("ml_obs.meta") or "{}")
        assert span_meta["session_id"] == "test_session_id"
    with LLMObs.task(session_id="test_session_id") as span:
        span_meta = json.loads(span.get_tag("ml_obs.meta") or "{}")
        assert span_meta["session_id"] == "test_session_id"
    with LLMObs.workflow(session_id="test_session_id") as span:
        span_meta = json.loads(span.get_tag("ml_obs.meta") or "{}")
        assert span_meta["session_id"] == "test_session_id"
    with LLMObs.agent(session_id="test_session_id") as span:
        span_meta = json.loads(span.get_tag("ml_obs.meta") or "{}")
        assert span_meta["session_id"] == "test_session_id"


def test_llmobs_session_id_becomes_top_level_field(LLMObs, mock_llmobs_writer):
    session_id = "test_session_id"
    with LLMObs.task(session_id=session_id) as span:
        pass
    mock_llmobs_writer.enqueue.assert_called_with(
        {
            "span_id": str(span.span_id),
            "trace_id": "{:x}".format(span.trace_id),
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
    with LLMObs.llm(model="test_model", name="test_llm_call", model_provider="test_provider") as span:
        assert span.name == "test_llm_call"
        assert span.resource == "llm"
        assert span.span_type == "llm"
        assert json.loads(span.get_tag("ml_obs.meta")) == {
            "span.kind": "llm",
            "model_name": "test_model",
            "model_provider": "test_provider",
            "session_id": None,
        }
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
            "meta": {"span.kind": "llm", "model_name": "test_model", "model_provider": "test_provider"},
            "metrics": mock.ANY,
        },
    )


def test_llmobs_llm_span_no_model_raises_error(LLMObs):
    with pytest.raises(TypeError):
        with LLMObs.llm(name="test_llm_call", model_provider="test_provider"):
            pass
    with pytest.raises(ValueError, match="model must be the specified name of the invoked model."):
        with LLMObs.llm(model="", name="test_llm_call", model_provider="test_provider"):
            pass


def test_llmobs_default_model_provider_set_to_custom(LLMObs):
    with LLMObs.llm(model="test_model", name="test_llm_call") as span:
        assert span.name == "test_llm_call"
        assert span.resource == "llm"
        assert span.span_type == "llm"
        assert json.loads(span.get_tag("ml_obs.meta")) == {
            "span.kind": "llm",
            "model_name": "test_model",
            "model_provider": "custom",
            "session_id": None,
        }


def test_llmobs_tool_span(LLMObs, mock_llmobs_writer):
    with LLMObs.tool(name="test_tool") as span:
        assert span.name == "test_tool"
        assert span.resource == "tool"
        assert span.span_type == "llm"
        assert json.loads(span.get_tag("ml_obs.meta")) == {"span.kind": "tool", "session_id": None}
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
        assert json.loads(span.get_tag("ml_obs.meta")) == {"span.kind": "task", "session_id": None}
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
        assert json.loads(span.get_tag("ml_obs.meta")) == {"span.kind": "workflow", "session_id": None}
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
        assert json.loads(span.get_tag("ml_obs.meta")) == {"span.kind": "agent", "session_id": None}
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


def test_llmobs_tag_while_disabled_raises_error(LLMObs):
    LLMObs.disable()
    with pytest.raises(Exception, match="cannot be used while LLMObs is disabled."):
        LLMObs.tag(parameters={"test": "test"})


def test_llmobs_tag_no_active_span_raises_error(LLMObs):
    with pytest.raises(Exception, match="No span provided and no active span found."):
        LLMObs.tag(parameters={"test": "test"})


def test_llmobs_tag_non_llm_span_raises_error(LLMObs):
    dummy_tracer = DummyTracer()
    with dummy_tracer.trace("root") as non_llmobs_span:
        with pytest.raises(ValueError, match="span must be an LLM-type span."):
            LLMObs.tag(span=non_llmobs_span, parameters={"test": "test"})


def test_llmobs_tag_finished_span_does_nothing(LLMObs, mock_logs):
    with LLMObs.llm(model="test_model", name="test_llm_call", model_provider="test_provider") as span:
        pass
    LLMObs.tag(span=span, parameters={"test": "test"})
    mock_logs.warning.assert_called_once_with("Cannot annotate a finished span.")


def test_llmobs_tag_meta_deserialize_error_raises(LLMObs):
    with LLMObs.task() as span:
        span.set_tag_str("ml_obs.meta", "{")
        with pytest.raises(ValueError, match="Failed to deserialize existing ml_obs.meta tag."):
            LLMObs.tag(span=span, parameters={"test": "test"})


def test_llmobs_tag_parameters(LLMObs):
    with LLMObs.llm(model="test_model", name="test_llm_call", model_provider="test_provider") as span:
        LLMObs.tag(span=span, parameters={"temperature": 0.9, "max_tokens": 50})
        span_meta = json.loads(span.get_tag("ml_obs.meta"))
        assert span_meta["input"]["parameters"] == {"temperature": 0.9, "max_tokens": 50}
        LLMObs.tag(span=span, parameters={"n": 1})
        span_meta = json.loads(span.get_tag("ml_obs.meta"))
        assert span_meta["input"]["parameters"] == {"temperature": 0.9, "max_tokens": 50, "n": 1}


def test_llmobs_tag_input_string(LLMObs):
    with LLMObs.llm(model="test_model") as llm_span:
        LLMObs.tag(span=llm_span, input_tags="test_input")
        span_meta = json.loads(llm_span.get_tag("ml_obs.meta"))
        assert span_meta["input"]["messages"] == [{"content": "test_input"}]
    with LLMObs.task() as task_span:
        LLMObs.tag(span=task_span, input_tags="test_input")
        span_meta = json.loads(task_span.get_tag("ml_obs.meta"))
        assert span_meta["input"]["value"] == "test_input"
    with LLMObs.tool() as tool_span:
        LLMObs.tag(span=tool_span, input_tags="test_input")
        span_meta = json.loads(tool_span.get_tag("ml_obs.meta"))
        assert span_meta["input"]["value"] == "test_input"
    with LLMObs.workflow() as workflow_span:
        LLMObs.tag(span=workflow_span, input_tags="test_input")
        span_meta = json.loads(workflow_span.get_tag("ml_obs.meta"))
        assert span_meta["input"]["value"] == "test_input"
    with LLMObs.agent() as agent_span:
        LLMObs.tag(span=agent_span, input_tags="test_input")
        span_meta = json.loads(agent_span.get_tag("ml_obs.meta"))
        assert span_meta["input"]["value"] == "test_input"


def test_llmobs_tag_input_llm_message(LLMObs):
    with LLMObs.llm(model="test_model") as llm_span:
        LLMObs.tag(span=llm_span, input_tags=[{"content": "test_input", "role": "human"}])
        span_meta = json.loads(llm_span.get_tag("ml_obs.meta"))
        assert span_meta["input"]["messages"] == [{"content": "test_input", "role": "human"}]


def test_llmobs_tag_non_llm_span_message_input_raises_error(LLMObs):
    with LLMObs.task() as span:
        with pytest.raises(ValueError, match="Invalid input/output type for non-llm span. Must be a raw string."):
            LLMObs.tag(span=span, input_tags=[{"content": "test_input"}])


def test_llmobs_tag_output_string(LLMObs):
    with LLMObs.llm(model="test_model") as llm_span:
        LLMObs.tag(span=llm_span, output_tags="test_output")
        span_meta = json.loads(llm_span.get_tag("ml_obs.meta"))
        assert span_meta["output"]["messages"] == [{"content": "test_output"}]
    with LLMObs.task() as task_span:
        LLMObs.tag(span=task_span, output_tags="test_output")
        span_meta = json.loads(task_span.get_tag("ml_obs.meta"))
        assert span_meta["output"]["value"] == "test_output"
    with LLMObs.tool() as tool_span:
        LLMObs.tag(span=tool_span, output_tags="test_output")
        span_meta = json.loads(tool_span.get_tag("ml_obs.meta"))
        assert span_meta["output"]["value"] == "test_output"
    with LLMObs.workflow() as workflow_span:
        LLMObs.tag(span=workflow_span, output_tags="test_output")
        span_meta = json.loads(workflow_span.get_tag("ml_obs.meta"))
        assert span_meta["output"]["value"] == "test_output"
    with LLMObs.agent() as agent_span:
        LLMObs.tag(span=agent_span, output_tags="test_output")
        span_meta = json.loads(agent_span.get_tag("ml_obs.meta"))
        assert span_meta["output"]["value"] == "test_output"


def test_llmobs_tag_output_llm_message(LLMObs):
    with LLMObs.llm(model="test_model") as llm_span:
        LLMObs.tag(span=llm_span, output_tags=[{"content": "test_output", "role": "human"}])
        span_meta = json.loads(llm_span.get_tag("ml_obs.meta"))
        assert span_meta["output"]["messages"] == [{"content": "test_output", "role": "human"}]


def test_llmobs_tag_non_llm_span_message_output_raises_error(LLMObs):
    with LLMObs.task() as span:
        with pytest.raises(ValueError, match="Invalid input/output type for non-llm span. Must be a raw string."):
            LLMObs.tag(span=span, output_tags=[{"content": "test_input"}])


def test_llmobs_span_error_sets_error_tag(LLMObs, mock_llmobs_writer):
    with pytest.raises(ValueError):
        with LLMObs.llm(model="test_model", model_provider="test_model_provider") as span:
            raise ValueError("test error message")
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
