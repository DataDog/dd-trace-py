import json

import mock
import pytest

from ddtrace.llmobs._constants import SPAN_START_WHILE_DISABLED_WARNING
from ddtrace.llmobs.decorators import agent
from ddtrace.llmobs.decorators import embedding
from ddtrace.llmobs.decorators import llm
from ddtrace.llmobs.decorators import retrieval
from ddtrace.llmobs.decorators import task
from ddtrace.llmobs.decorators import tool
from ddtrace.llmobs.decorators import workflow
from tests.llmobs._utils import _expected_llmobs_llm_span_event
from tests.llmobs._utils import _expected_llmobs_non_llm_span_event


@pytest.fixture
def mock_logs():
    with mock.patch("ddtrace.llmobs.decorators.log") as mock_logs:
        yield mock_logs


def test_llm_decorator_with_llmobs_disabled_logs_warning(LLMObs, mock_logs):
    for decorator_name, decorator in (("llm", llm), ("embedding", embedding)):

        @decorator(
            model_name="test_model", model_provider="test_provider", name="test_function", session_id="test_session_id"
        )
        def f():
            pass

        LLMObs.disable()
        f()
        mock_logs.warning.assert_called_with(SPAN_START_WHILE_DISABLED_WARNING)
        mock_logs.reset_mock()


def test_non_llm_decorator_with_llmobs_disabled_logs_warning(LLMObs, mock_logs):
    for decorator_name, decorator in (
        ("task", task),
        ("workflow", workflow),
        ("tool", tool),
        ("agent", agent),
        ("retrieval", retrieval),
    ):

        @decorator(name="test_function", session_id="test_session_id")
        def f():
            pass

        LLMObs.disable()
        f()
        mock_logs.warning.assert_called_with(SPAN_START_WHILE_DISABLED_WARNING)
        mock_logs.reset_mock()


def test_llm_decorator(LLMObs, mock_llmobs_span_writer):
    @llm(model_name="test_model", model_provider="test_provider", name="test_function", session_id="test_session_id")
    def f():
        pass

    f()
    span = LLMObs._instance.tracer.pop()[0]
    mock_llmobs_span_writer.enqueue.assert_called_with(
        _expected_llmobs_llm_span_event(
            span, "llm", model_name="test_model", model_provider="test_provider", session_id="test_session_id"
        )
    )


def test_llm_decorator_no_model_name_raises_error(LLMObs, mock_llmobs_span_writer):
    with pytest.raises(TypeError):

        @llm(model_provider="test_provider", name="test_function", session_id="test_session_id")
        def f():
            pass


def test_llm_decorator_default_kwargs(LLMObs, mock_llmobs_span_writer):
    @llm(model_name="test_model")
    def f():
        pass

    f()
    span = LLMObs._instance.tracer.pop()[0]
    mock_llmobs_span_writer.enqueue.assert_called_with(
        _expected_llmobs_llm_span_event(span, "llm", model_name="test_model", model_provider="custom")
    )


def test_embedding_decorator(LLMObs, mock_llmobs_span_writer):
    @embedding(
        model_name="test_model", model_provider="test_provider", name="test_function", session_id="test_session_id"
    )
    def f():
        pass

    f()
    span = LLMObs._instance.tracer.pop()[0]
    mock_llmobs_span_writer.enqueue.assert_called_with(
        _expected_llmobs_llm_span_event(
            span, "embedding", model_name="test_model", model_provider="test_provider", session_id="test_session_id"
        )
    )


def test_embedding_decorator_no_model_name_raises_error(LLMObs):
    with pytest.raises(TypeError):

        @embedding(model_provider="test_provider", name="test_function", session_id="test_session_id")
        def f():
            pass


def test_embedding_decorator_default_kwargs(LLMObs, mock_llmobs_span_writer):
    @embedding(model_name="test_model")
    def f():
        pass

    f()
    span = LLMObs._instance.tracer.pop()[0]
    mock_llmobs_span_writer.enqueue.assert_called_with(
        _expected_llmobs_llm_span_event(span, "embedding", model_name="test_model", model_provider="custom")
    )


def test_retrieval_decorator(LLMObs, mock_llmobs_span_writer):
    @retrieval(name="test_function", session_id="test_session_id")
    def f():
        pass

    f()
    span = LLMObs._instance.tracer.pop()[0]
    mock_llmobs_span_writer.enqueue.assert_called_with(
        _expected_llmobs_non_llm_span_event(span, "retrieval", session_id="test_session_id")
    )


def test_retrieval_decorator_default_kwargs(LLMObs, mock_llmobs_span_writer):
    @retrieval()
    def f():
        pass

    f()
    span = LLMObs._instance.tracer.pop()[0]
    mock_llmobs_span_writer.enqueue.assert_called_with(_expected_llmobs_non_llm_span_event(span, "retrieval"))


def test_task_decorator(LLMObs, mock_llmobs_span_writer):
    @task(name="test_function", session_id="test_session_id")
    def f():
        pass

    f()
    span = LLMObs._instance.tracer.pop()[0]
    mock_llmobs_span_writer.enqueue.assert_called_with(
        _expected_llmobs_non_llm_span_event(span, "task", session_id="test_session_id")
    )


def test_task_decorator_default_kwargs(LLMObs, mock_llmobs_span_writer):
    @task()
    def f():
        pass

    f()
    span = LLMObs._instance.tracer.pop()[0]
    mock_llmobs_span_writer.enqueue.assert_called_with(_expected_llmobs_non_llm_span_event(span, "task"))


def test_tool_decorator(LLMObs, mock_llmobs_span_writer):
    @tool(name="test_function", session_id="test_session_id")
    def f():
        pass

    f()
    span = LLMObs._instance.tracer.pop()[0]
    mock_llmobs_span_writer.enqueue.assert_called_with(
        _expected_llmobs_non_llm_span_event(span, "tool", session_id="test_session_id")
    )


def test_tool_decorator_default_kwargs(LLMObs, mock_llmobs_span_writer):
    @tool()
    def f():
        pass

    f()
    span = LLMObs._instance.tracer.pop()[0]
    mock_llmobs_span_writer.enqueue.assert_called_with(_expected_llmobs_non_llm_span_event(span, "tool"))


def test_workflow_decorator(LLMObs, mock_llmobs_span_writer):
    @workflow(name="test_function", session_id="test_session_id")
    def f():
        pass

    f()
    span = LLMObs._instance.tracer.pop()[0]
    mock_llmobs_span_writer.enqueue.assert_called_with(
        _expected_llmobs_non_llm_span_event(span, "workflow", session_id="test_session_id")
    )


def test_workflow_decorator_default_kwargs(LLMObs, mock_llmobs_span_writer):
    @workflow()
    def f():
        pass

    f()
    span = LLMObs._instance.tracer.pop()[0]
    mock_llmobs_span_writer.enqueue.assert_called_with(_expected_llmobs_non_llm_span_event(span, "workflow"))


def test_agent_decorator(LLMObs, mock_llmobs_span_writer):
    @agent(name="test_function", session_id="test_session_id")
    def f():
        pass

    f()
    span = LLMObs._instance.tracer.pop()[0]
    mock_llmobs_span_writer.enqueue.assert_called_with(
        _expected_llmobs_llm_span_event(span, "agent", session_id="test_session_id")
    )


def test_agent_decorator_default_kwargs(LLMObs, mock_llmobs_span_writer):
    @agent()
    def f():
        pass

    f()
    span = LLMObs._instance.tracer.pop()[0]
    mock_llmobs_span_writer.enqueue.assert_called_with(_expected_llmobs_llm_span_event(span, "agent"))


def test_llm_decorator_with_error(LLMObs, mock_llmobs_span_writer):
    @llm(model_name="test_model", model_provider="test_provider", name="test_function", session_id="test_session_id")
    def f():
        raise ValueError("test_error")

    with pytest.raises(ValueError):
        f()
    span = LLMObs._instance.tracer.pop()[0]
    mock_llmobs_span_writer.enqueue.assert_called_with(
        _expected_llmobs_llm_span_event(
            span,
            "llm",
            model_name="test_model",
            model_provider="test_provider",
            session_id="test_session_id",
            error=span.get_tag("error.type"),
            error_message=span.get_tag("error.message"),
            error_stack=span.get_tag("error.stack"),
        )
    )


def test_non_llm_decorators_with_error(LLMObs, mock_llmobs_span_writer):
    for decorator_name, decorator in [("task", task), ("workflow", workflow), ("tool", tool), ("agent", agent)]:

        @decorator(name="test_function", session_id="test_session_id")
        def f():
            raise ValueError("test_error")

        with pytest.raises(ValueError):
            f()
        span = LLMObs._instance.tracer.pop()[0]
        mock_llmobs_span_writer.enqueue.assert_called_with(
            _expected_llmobs_non_llm_span_event(
                span,
                decorator_name,
                session_id="test_session_id",
                error=span.get_tag("error.type"),
                error_message=span.get_tag("error.message"),
                error_stack=span.get_tag("error.stack"),
            )
        )


def test_llm_annotate(LLMObs, mock_llmobs_span_writer):
    @llm(model_name="test_model", model_provider="test_provider", name="test_function", session_id="test_session_id")
    def f():
        LLMObs.annotate(
            parameters={"temperature": 0.9, "max_tokens": 50},
            input_data=[{"content": "test_prompt"}],
            output_data=[{"content": "test_response"}],
            tags={"custom_tag": "tag_value"},
            metrics={"input_tokens": 10, "output_tokens": 20, "total_tokens": 30},
        )

    f()
    span = LLMObs._instance.tracer.pop()[0]
    mock_llmobs_span_writer.enqueue.assert_called_with(
        _expected_llmobs_llm_span_event(
            span,
            "llm",
            model_name="test_model",
            model_provider="test_provider",
            input_messages=[{"content": "test_prompt"}],
            output_messages=[{"content": "test_response"}],
            parameters={"temperature": 0.9, "max_tokens": 50},
            token_metrics={"input_tokens": 10, "output_tokens": 20, "total_tokens": 30},
            tags={"custom_tag": "tag_value"},
            session_id="test_session_id",
        )
    )


def test_llm_annotate_raw_string_io(LLMObs, mock_llmobs_span_writer):
    @llm(model_name="test_model", model_provider="test_provider", name="test_function", session_id="test_session_id")
    def f():
        LLMObs.annotate(
            parameters={"temperature": 0.9, "max_tokens": 50},
            input_data="test_prompt",
            output_data="test_response",
            tags={"custom_tag": "tag_value"},
            metrics={"input_tokens": 10, "output_tokens": 20, "total_tokens": 30},
        )

    f()
    span = LLMObs._instance.tracer.pop()[0]
    mock_llmobs_span_writer.enqueue.assert_called_with(
        _expected_llmobs_llm_span_event(
            span,
            "llm",
            model_name="test_model",
            model_provider="test_provider",
            input_messages=[{"content": "test_prompt"}],
            output_messages=[{"content": "test_response"}],
            parameters={"temperature": 0.9, "max_tokens": 50},
            token_metrics={"input_tokens": 10, "output_tokens": 20, "total_tokens": 30},
            tags={"custom_tag": "tag_value"},
            session_id="test_session_id",
        )
    )


def test_non_llm_decorators_no_args(LLMObs, mock_llmobs_span_writer):
    """Test that using the decorators without any arguments, i.e. @tool, works the same as @tool(...)."""
    for decorator_name, decorator in [
        ("task", task),
        ("workflow", workflow),
        ("tool", tool),
        ("agent", agent),
        ("retrieval", retrieval),
    ]:

        @decorator
        def f():
            pass

        f()
        span = LLMObs._instance.tracer.pop()[0]
        mock_llmobs_span_writer.enqueue.assert_called_with(_expected_llmobs_non_llm_span_event(span, decorator_name))


def test_agent_decorator_no_args(LLMObs, mock_llmobs_span_writer):
    """Test that using agent decorator without any arguments, i.e. @agent, works the same as @agent(...)."""

    @agent
    def f():
        pass

    f()
    span = LLMObs._instance.tracer.pop()[0]
    mock_llmobs_span_writer.enqueue.assert_called_with(_expected_llmobs_llm_span_event(span, "agent"))


def test_ml_app_override(LLMObs, mock_llmobs_span_writer):
    """Test that setting ml_app kwarg on the LLMObs decorators will override the DD_LLMOBS_ML_APP value."""
    for decorator_name, decorator in [("task", task), ("workflow", workflow), ("tool", tool)]:

        @decorator(ml_app="test_ml_app")
        def f():
            pass

        f()
        span = LLMObs._instance.tracer.pop()[0]
        mock_llmobs_span_writer.enqueue.assert_called_with(
            _expected_llmobs_non_llm_span_event(span, decorator_name, tags={"ml_app": "test_ml_app"})
        )

    @llm(model_name="test_model", ml_app="test_ml_app")
    def g():
        pass

    g()
    span = LLMObs._instance.tracer.pop()[0]
    mock_llmobs_span_writer.enqueue.assert_called_with(
        _expected_llmobs_llm_span_event(
            span, "llm", model_name="test_model", model_provider="custom", tags={"ml_app": "test_ml_app"}
        )
    )

    @embedding(model_name="test_model", ml_app="test_ml_app")
    def h():
        pass

    h()
    span = LLMObs._instance.tracer.pop()[0]
    mock_llmobs_span_writer.enqueue.assert_called_with(
        _expected_llmobs_llm_span_event(
            span, "embedding", model_name="test_model", model_provider="custom", tags={"ml_app": "test_ml_app"}
        )
    )


async def test_non_llm_async_decorators(LLMObs, mock_llmobs_span_writer):
    """Test that decorators work with async functions."""
    for decorator_name, decorator in [
        ("task", task),
        ("workflow", workflow),
        ("tool", tool),
        ("agent", agent),
        ("retrieval", retrieval),
    ]:

        @decorator
        async def f():
            pass

        await f()
        span = LLMObs._instance.tracer.pop()[0]
        mock_llmobs_span_writer.enqueue.assert_called_with(_expected_llmobs_non_llm_span_event(span, decorator_name))


async def test_llm_async_decorators(LLMObs, mock_llmobs_span_writer):
    """Test that decorators work with async functions."""
    for decorator_name, decorator in [("llm", llm), ("embedding", embedding)]:

        @decorator(model_name="test_model", model_provider="test_provider")
        async def f():
            pass

        await f()
        span = LLMObs._instance.tracer.pop()[0]
        mock_llmobs_span_writer.enqueue.assert_called_with(
            _expected_llmobs_llm_span_event(
                span, decorator_name, model_name="test_model", model_provider="test_provider"
            )
        )


def test_automatic_annotation_non_llm_decorators(LLMObs, mock_llmobs_span_writer):
    """Test that automatic input/output annotation works for non-LLM decorators."""
    for decorator_name, decorator in (("task", task), ("workflow", workflow), ("tool", tool), ("agent", agent)):

        @decorator(name="test_function", session_id="test_session_id")
        def f(prompt, arg_2, kwarg_1=None, kwarg_2=None):
            return prompt

        f("test_prompt", "arg_2", kwarg_2=12345)
        span = LLMObs._instance.tracer.pop()[0]
        mock_llmobs_span_writer.enqueue.assert_called_with(
            _expected_llmobs_non_llm_span_event(
                span,
                decorator_name,
                input_value=json.dumps({"prompt": "test_prompt", "arg_2": "arg_2", "kwarg_2": 12345}),
                output_value="test_prompt",
                session_id="test_session_id",
            )
        )


def test_automatic_annotation_retrieval_decorator(LLMObs, mock_llmobs_span_writer):
    """Test that automatic input annotation works for retrieval decorators."""

    @retrieval(session_id="test_session_id")
    def test_retrieval(query, arg_2, kwarg_1=None, kwarg_2=None):
        return [{"name": "name", "id": "1234567890", "score": 0.9}]

    test_retrieval("test_query", "arg_2", kwarg_2=12345)
    span = LLMObs._instance.tracer.pop()[0]
    mock_llmobs_span_writer.enqueue.assert_called_with(
        _expected_llmobs_non_llm_span_event(
            span,
            "retrieval",
            input_value=json.dumps({"query": "test_query", "arg_2": "arg_2", "kwarg_2": 12345}),
            session_id="test_session_id",
        )
    )


def test_automatic_annotation_off_non_llm_decorators(LLMObs, mock_llmobs_span_writer):
    """Test disabling automatic input/output annotation for non-LLM decorators."""
    for decorator_name, decorator in (
        ("task", task),
        ("workflow", workflow),
        ("tool", tool),
        ("retrieval", retrieval),
        ("agent", agent),
    ):

        @decorator(name="test_function", session_id="test_session_id", _automatic_io_annotation=False)
        def f(prompt, arg_2, kwarg_1=None, kwarg_2=None):
            return prompt

        f("test_prompt", "arg_2", kwarg_2=12345)
        span = LLMObs._instance.tracer.pop()[0]
        mock_llmobs_span_writer.enqueue.assert_called_with(
            _expected_llmobs_non_llm_span_event(span, decorator_name, session_id="test_session_id")
        )


def test_automatic_annotation_off_if_manually_annotated(LLMObs, mock_llmobs_span_writer):
    """Test disabling automatic input/output annotation for non-LLM decorators."""
    for decorator_name, decorator in (("task", task), ("workflow", workflow), ("tool", tool), ("agent", agent)):

        @decorator(name="test_function", session_id="test_session_id")
        def f(prompt, arg_2, kwarg_1=None, kwarg_2=None):
            LLMObs.annotate(input_data="my custom input", output_data="my custom output")
            return prompt

        f("test_prompt", "arg_2", kwarg_2=12345)
        span = LLMObs._instance.tracer.pop()[0]
        mock_llmobs_span_writer.enqueue.assert_called_with(
            _expected_llmobs_non_llm_span_event(
                span,
                decorator_name,
                session_id="test_session_id",
                input_value="my custom input",
                output_value="my custom output",
            )
        )
