import mock
import pytest

from ddtrace.llmobs.decorators import agent
from ddtrace.llmobs.decorators import llm
from ddtrace.llmobs.decorators import task
from ddtrace.llmobs.decorators import tool
from ddtrace.llmobs.decorators import workflow


@pytest.fixture
def mock_logs():
    with mock.patch("ddtrace.llmobs.decorators.log") as mock_logs:
        yield mock_logs


def _expected_llmobs_tags(error=None, tags=None):
    expected_tags = [
        "version:",
        "env:",
        "service:",
        "source:integration",
        "ml_app:unnamed-ml-app",
    ]
    if error:
        expected_tags.append("error:1")
        expected_tags.append("error_type:{}".format(error))
    else:
        expected_tags.append("error:0")
    if tags:
        expected_tags.extend("{}:{}".format(k, v) for k, v in tags.items())
    return expected_tags


def test_llm_decorator_with_llmobs_disabled_logs_warning(LLMObs, mock_logs):
    @llm(model_name="test_model", model_provider="test_provider", name="test_function", session_id="test_session_id")
    def f():
        pass
    LLMObs.disable()
    f()
    mock_logs.warning.assert_called_with("LLMObs.llm() cannot be used while LLMObs is disabled.")


def test_non_llm_decorator_with_llmobs_disabled_logs_warning(LLMObs, mock_logs):
    for decorator_name, decorator in [("task", task), ("workflow", workflow), ("tool", tool), ("agent", agent)]:
        @decorator(name="test_function", session_id="test_session_id")
        def f():
            pass
        LLMObs.disable()
        f()
        mock_logs.warning.assert_called_with(
            "LLMObs.{}() cannot be used while LLMObs is disabled.".format(decorator_name)
        )
        mock_logs.reset_mock()


def test_llm_decorator(LLMObs, mock_llmobs_writer):
    @llm(model_name="test_model", model_provider="test_provider", name="test_function", session_id="test_session_id")
    def f():
        pass

    f()
    span = LLMObs._instance.tracer.pop()[0]
    mock_llmobs_writer.enqueue.assert_called_with(
        {
            "trace_id": "{:x}".format(span.trace_id),
            "span_id": str(span.span_id),
            "parent_id": "",
            "session_id": "test_session_id",
            "name": "test_function",
            "tags": _expected_llmobs_tags(),
            "start_ns": span.start_ns,
            "duration": span.duration_ns,
            "error": 0,
            "meta": {"span.kind": "llm", "model_name": "test_model", "model_provider": "test_provider"},
            "metrics": {},
        },
    )


def test_llm_decorator_no_model_name_raises_error(LLMObs, mock_llmobs_writer):
    with pytest.raises(TypeError):

        @llm(model_provider="test_provider", name="test_function", session_id="test_session_id")
        def f():
            pass


def test_llm_decorator_default_kwargs(LLMObs, mock_llmobs_writer):
    @llm(model_name="test_model")
    def f():
        pass

    f()
    span = LLMObs._instance.tracer.pop()[0]
    mock_llmobs_writer.enqueue.assert_called_with(
        {
            "trace_id": "{:x}".format(span.trace_id),
            "span_id": str(span.span_id),
            "parent_id": "",
            "session_id": "{:x}".format(span.trace_id),
            "name": "f",
            "tags": _expected_llmobs_tags(),
            "start_ns": span.start_ns,
            "duration": span.duration_ns,
            "error": 0,
            "meta": {"span.kind": "llm", "model_name": "test_model", "model_provider": "custom"},
            "metrics": {},
        },
    )


def test_task_decorator(LLMObs, mock_llmobs_writer):
    @task(name="test_function", session_id="test_session_id")
    def f():
        pass

    f()
    span = LLMObs._instance.tracer.pop()[0]
    mock_llmobs_writer.enqueue.assert_called_with(
        {
            "trace_id": "{:x}".format(span.trace_id),
            "span_id": str(span.span_id),
            "parent_id": "",
            "session_id": "test_session_id",
            "name": "test_function",
            "tags": _expected_llmobs_tags(),
            "start_ns": span.start_ns,
            "duration": span.duration_ns,
            "error": 0,
            "meta": {"span.kind": "task"},
            "metrics": {},
        },
    )


def test_task_decorator_default_kwargs(LLMObs, mock_llmobs_writer):
    @task()
    def f():
        pass

    f()
    span = LLMObs._instance.tracer.pop()[0]
    mock_llmobs_writer.enqueue.assert_called_with(
        {
            "trace_id": "{:x}".format(span.trace_id),
            "span_id": str(span.span_id),
            "parent_id": "",
            "session_id": "{:x}".format(span.trace_id),
            "name": "f",
            "tags": _expected_llmobs_tags(),
            "start_ns": span.start_ns,
            "duration": span.duration_ns,
            "error": 0,
            "meta": {"span.kind": "task"},
            "metrics": {},
        },
    )


def test_tool_decorator(LLMObs, mock_llmobs_writer):
    @tool(name="test_function", session_id="test_session_id")
    def f():
        pass

    f()
    span = LLMObs._instance.tracer.pop()[0]
    mock_llmobs_writer.enqueue.assert_called_with(
        {
            "trace_id": "{:x}".format(span.trace_id),
            "span_id": str(span.span_id),
            "parent_id": "",
            "session_id": "test_session_id",
            "name": "test_function",
            "tags": _expected_llmobs_tags(),
            "start_ns": span.start_ns,
            "duration": span.duration_ns,
            "error": 0,
            "meta": {"span.kind": "tool"},
            "metrics": {},
        },
    )


def test_tool_decorator_default_kwargs(LLMObs, mock_llmobs_writer):
    @tool()
    def f():
        pass

    f()
    span = LLMObs._instance.tracer.pop()[0]
    mock_llmobs_writer.enqueue.assert_called_with(
        {
            "trace_id": "{:x}".format(span.trace_id),
            "span_id": str(span.span_id),
            "parent_id": "",
            "session_id": "{:x}".format(span.trace_id),
            "name": "f",
            "tags": _expected_llmobs_tags(),
            "start_ns": span.start_ns,
            "duration": span.duration_ns,
            "error": 0,
            "meta": {"span.kind": "tool"},
            "metrics": {},
        },
    )


def test_workflow_decorator(LLMObs, mock_llmobs_writer):
    @workflow(name="test_function", session_id="test_session_id")
    def f():
        pass

    f()
    span = LLMObs._instance.tracer.pop()[0]
    mock_llmobs_writer.enqueue.assert_called_with(
        {
            "trace_id": "{:x}".format(span.trace_id),
            "span_id": str(span.span_id),
            "parent_id": "",
            "session_id": "test_session_id",
            "name": "test_function",
            "tags": _expected_llmobs_tags(),
            "start_ns": span.start_ns,
            "duration": span.duration_ns,
            "error": 0,
            "meta": {"span.kind": "workflow"},
            "metrics": {},
        },
    )


def test_workflow_decorator_default_kwargs(LLMObs, mock_llmobs_writer):
    @workflow()
    def f():
        pass

    f()
    span = LLMObs._instance.tracer.pop()[0]
    mock_llmobs_writer.enqueue.assert_called_with(
        {
            "trace_id": "{:x}".format(span.trace_id),
            "span_id": str(span.span_id),
            "parent_id": "",
            "session_id": "{:x}".format(span.trace_id),
            "name": "f",
            "tags": _expected_llmobs_tags(),
            "start_ns": span.start_ns,
            "duration": span.duration_ns,
            "error": 0,
            "meta": {"span.kind": "workflow"},
            "metrics": {},
        },
    )


def test_agent_decorator(LLMObs, mock_llmobs_writer):
    @agent(name="test_function", session_id="test_session_id")
    def f():
        pass

    f()
    span = LLMObs._instance.tracer.pop()[0]
    mock_llmobs_writer.enqueue.assert_called_with(
        {
            "trace_id": "{:x}".format(span.trace_id),
            "span_id": str(span.span_id),
            "parent_id": "",
            "session_id": "test_session_id",
            "name": "test_function",
            "tags": _expected_llmobs_tags(),
            "start_ns": span.start_ns,
            "duration": span.duration_ns,
            "error": 0,
            "meta": {"span.kind": "agent"},
            "metrics": {},
        },
    )


def test_agent_decorator_default_kwargs(LLMObs, mock_llmobs_writer):
    @agent()
    def f():
        pass

    f()
    span = LLMObs._instance.tracer.pop()[0]
    mock_llmobs_writer.enqueue.assert_called_with(
        {
            "trace_id": "{:x}".format(span.trace_id),
            "span_id": str(span.span_id),
            "parent_id": "",
            "session_id": "{:x}".format(span.trace_id),
            "name": "f",
            "tags": _expected_llmobs_tags(),
            "start_ns": span.start_ns,
            "duration": span.duration_ns,
            "error": 0,
            "meta": {"span.kind": "agent"},
            "metrics": {},
        },
    )


def test_llm_decorator_with_error(LLMObs, mock_llmobs_writer):
    @llm(model_name="test_model", model_provider="test_provider", name="test_function", session_id="test_session_id")
    def f():
        raise ValueError("test_error")

    with pytest.raises(ValueError):
        f()
    span = LLMObs._instance.tracer.pop()[0]
    mock_llmobs_writer.enqueue.assert_called_with(
        {
            "trace_id": "{:x}".format(span.trace_id),
            "span_id": str(span.span_id),
            "parent_id": "",
            "session_id": "test_session_id",
            "name": "test_function",
            "tags": _expected_llmobs_tags("builtins.ValueError"),
            "start_ns": span.start_ns,
            "duration": span.duration_ns,
            "error": 1,
            "meta": {
                "span.kind": "llm",
                "model_name": "test_model",
                "model_provider": "test_provider",
                "error.message": "test_error",
            },
            "metrics": {},
        },
    )


def test_non_llm_decorators_with_error(LLMObs, mock_llmobs_writer):
    for decorator_name, decorator in [("task", task), ("workflow", workflow), ("tool", tool), ("agent", agent)]:

        @decorator(name="test_function", session_id="test_session_id")
        def f():
            raise ValueError("test_error")

        with pytest.raises(ValueError):
            f()
        span = LLMObs._instance.tracer.pop()[0]
        mock_llmobs_writer.enqueue.assert_called_with(
            {
                "trace_id": "{:x}".format(span.trace_id),
                "span_id": str(span.span_id),
                "parent_id": "",
                "session_id": "test_session_id",
                "name": "test_function",
                "tags": _expected_llmobs_tags("builtins.ValueError"),
                "start_ns": span.start_ns,
                "duration": span.duration_ns,
                "error": 1,
                "meta": {"span.kind": decorator_name, "error.message": "test_error"},
                "metrics": {},
            },
        )


def test_llm_annotate(LLMObs, mock_llmobs_writer):
    @llm(model_name="test_model", model_provider="test_provider", name="test_function", session_id="test_session_id")
    def f():
        LLMObs.annotate(
            parameters={"temperature": 0.9, "max_tokens": 50},
            input_data=[{"content": "test_prompt"}],
            output_data=[{"content": "test_response"}],
            tags={"custom_tag": "tag_value"},
            metrics={"prompt_tokens": 10, "completion_tokens": 20, "total_tokens": 30},
        )

    f()
    span = LLMObs._instance.tracer.pop()[0]
    mock_llmobs_writer.enqueue.assert_called_with(
        {
            "trace_id": "{:x}".format(span.trace_id),
            "span_id": str(span.span_id),
            "parent_id": "",
            "session_id": "test_session_id",
            "name": "test_function",
            "tags": _expected_llmobs_tags(tags={"custom_tag": "tag_value"}),
            "start_ns": span.start_ns,
            "duration": span.duration_ns,
            "error": 0,
            "meta": {
                "span.kind": "llm",
                "model_name": "test_model",
                "model_provider": "test_provider",
                "input": {
                    "parameters": {"temperature": 0.9, "max_tokens": 50},
                    "messages": [{"content": "test_prompt"}],
                },
                "output": {"messages": [{"content": "test_response"}]},
            },
            "metrics": {"prompt_tokens": 10, "completion_tokens": 20, "total_tokens": 30},
        },
    )


def test_llm_annotate_raw_string_io(LLMObs, mock_llmobs_writer):
    @llm(model_name="test_model", model_provider="test_provider", name="test_function", session_id="test_session_id")
    def f():
        LLMObs.annotate(
            parameters={"temperature": 0.9, "max_tokens": 50},
            input_data="test_prompt",
            output_data="test_response",
            tags={"custom_tag": "tag_value"},
            metrics={"prompt_tokens": 10, "completion_tokens": 20, "total_tokens": 30},
        )

    f()
    span = LLMObs._instance.tracer.pop()[0]
    mock_llmobs_writer.enqueue.assert_called_with(
        {
            "trace_id": "{:x}".format(span.trace_id),
            "span_id": str(span.span_id),
            "parent_id": "",
            "session_id": "test_session_id",
            "name": "test_function",
            "tags": _expected_llmobs_tags(tags={"custom_tag": "tag_value"}),
            "start_ns": span.start_ns,
            "duration": span.duration_ns,
            "error": 0,
            "meta": {
                "span.kind": "llm",
                "model_name": "test_model",
                "model_provider": "test_provider",
                "input": {
                    "parameters": {"temperature": 0.9, "max_tokens": 50},
                    "messages": [{"content": "test_prompt"}],
                },
                "output": {"messages": [{"content": "test_response"}]},
            },
            "metrics": {"prompt_tokens": 10, "completion_tokens": 20, "total_tokens": 30},
        },
    )
