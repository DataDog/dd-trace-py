import mock
import pytest

from ddtrace.llmobs.decorators import agent
from ddtrace.llmobs.decorators import llm
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
    @llm(model_name="test_model", model_provider="test_provider", name="test_function", session_id="test_session_id")
    def f():
        pass

    LLMObs.disable()
    f()
    mock_logs.warning.assert_called_with("LLMObs.llm() cannot be used while LLMObs is disabled.")


def test_llm_gen_decorator_with_llmobs_disabled(LLMObs, mock_logs):
    # make sure the decorator doesn't break generator when LLMObs is disabled
    sent = None
    cleanup = None
    valerror = None

    @llm(model_name="test_model", model_provider="test_provider", name="test_function", session_id="test_session_id")
    def f():
        nonlocal sent
        nonlocal cleanup
        nonlocal valerror
        try:
            for i in range(10):
                try:
                    g = yield i
                    if g:
                        sent = g
                except ValueError:
                    valerror = "valerror"
        except GeneratorExit:
            cleanup = "cleanup"
            raise GeneratorExit

    LLMObs.disable()
    gen = f()
    for i in gen:
        if i == 5:
            gen.send("hi")
            gen.throw(ValueError)
            gen.close()

    assert sent == "hi"
    assert cleanup == "cleanup"
    assert valerror == "valerror"
    mock_logs.warning.assert_called_with("LLMObs.llm() cannot be used while LLMObs is disabled.")


def test_non_llm_gen_decorator_with_llmobs_disabled(LLMObs, mock_logs):
    # make sure the decorator doesn't break generator when LLMObs is disabled
    for decorator_name, decorator in [("task", task), ("workflow", workflow), ("tool", tool), ("agent", agent)]:
        sent = None
        cleanup = None
        valerror = None

        @decorator(name="test_function", session_id="test_session_id")
        def f():
            nonlocal sent
            nonlocal cleanup
            nonlocal valerror
            try:
                for i in range(10):
                    try:
                        g = yield i
                        if g:
                            sent = g
                    except ValueError:
                        valerror = "valerror"
            except GeneratorExit:
                cleanup = "cleanup"
                raise GeneratorExit

        LLMObs.disable()
        gen = f()
        for i in gen:
            if i == 5:
                gen.send("hi")
                gen.throw(ValueError)
                gen.close()

        assert sent == "hi"
        assert cleanup == "cleanup"
        assert valerror == "valerror"

        mock_logs.warning.assert_called_with("LLMObs.{}() cannot be used while LLMObs is disabled.", decorator_name)
        mock_logs.reset_mock()


def test_non_llm_decorator_with_llmobs_disabled_logs_warning(LLMObs, mock_logs):
    for decorator_name, decorator in [("task", task), ("workflow", workflow), ("tool", tool), ("agent", agent)]:

        @decorator(name="test_function", session_id="test_session_id")
        def f():
            pass

        LLMObs.disable()
        f()
        mock_logs.warning.assert_called_with("LLMObs.{}() cannot be used while LLMObs is disabled.", decorator_name)
        mock_logs.reset_mock()


def test_llm_decorator(LLMObs, mock_llmobs_writer):
    @llm(model_name="test_model", model_provider="test_provider", name="test_function", session_id="test_session_id")
    def f():
        pass

    f()
    span = LLMObs._instance.tracer.pop()[0]
    mock_llmobs_writer.enqueue.assert_called_with(
        _expected_llmobs_llm_span_event(
            span, "llm", model_name="test_model", model_provider="test_provider", session_id="test_session_id"
        )
    )


def test_llm_decorator_gen(LLMObs, mock_llmobs_writer):
    @llm(model_name="test_model", model_provider="test_provider", name="test_function", session_id="test_session_id")
    def f_gen():
        for i in range(10):
            yield i

    for _ in f_gen():
        pass
    span = LLMObs._instance.tracer.pop()[0]
    mock_llmobs_writer.enqueue.assert_called_with(
        _expected_llmobs_llm_span_event(
            span, "llm", model_name="test_model", model_provider="test_provider", session_id="test_session_id"
        )
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
        _expected_llmobs_llm_span_event(span, "llm", model_name="test_model", model_provider="custom")
    )


def test_llm_decorator_gen_default_kwargs(LLMObs, mock_llmobs_writer):
    @llm(model_name="test_model")
    def f():
        for i in range(10):
            yield i

    for _ in f():
        pass
    span = LLMObs._instance.tracer.pop()[0]
    mock_llmobs_writer.enqueue.assert_called_with(
        _expected_llmobs_llm_span_event(span, "llm", model_name="test_model", model_provider="custom")
    )


def test_task_decorator(LLMObs, mock_llmobs_writer):
    @task(name="test_function", session_id="test_session_id")
    def f():
        pass

    f()
    span = LLMObs._instance.tracer.pop()[0]
    mock_llmobs_writer.enqueue.assert_called_with(
        _expected_llmobs_non_llm_span_event(span, "task", session_id="test_session_id")
    )


def test_task_decorator_gen(LLMObs, mock_llmobs_writer):
    @task(name="test_function", session_id="test_session_id")
    def f():
        for i in range(10):
            yield i

    for _ in f():
        pass
    span = LLMObs._instance.tracer.pop()[0]
    mock_llmobs_writer.enqueue.assert_called_with(
        _expected_llmobs_non_llm_span_event(span, "task", session_id="test_session_id")
    )


def test_task_decorator_default_kwargs(LLMObs, mock_llmobs_writer):
    @task()
    def f():
        pass

    f()
    span = LLMObs._instance.tracer.pop()[0]
    mock_llmobs_writer.enqueue.assert_called_with(_expected_llmobs_non_llm_span_event(span, "task"))


def test_task_decorator_gen_default_kwargs(LLMObs, mock_llmobs_writer):
    @task()
    def f():
        for i in range(10):
            yield i

    for _ in f():
        pass
    span = LLMObs._instance.tracer.pop()[0]
    mock_llmobs_writer.enqueue.assert_called_with(_expected_llmobs_non_llm_span_event(span, "task"))


def test_tool_decorator(LLMObs, mock_llmobs_writer):
    @tool(name="test_function", session_id="test_session_id")
    def f():
        pass

    f()
    span = LLMObs._instance.tracer.pop()[0]
    mock_llmobs_writer.enqueue.assert_called_with(
        _expected_llmobs_non_llm_span_event(span, "tool", session_id="test_session_id")
    )


def test_tool_decorator_gen(LLMObs, mock_llmobs_writer):
    @tool(name="test_function", session_id="test_session_id")
    def f():
        for i in range(10):
            yield i

    for _ in f():
        pass
    span = LLMObs._instance.tracer.pop()[0]
    mock_llmobs_writer.enqueue.assert_called_with(
        _expected_llmobs_non_llm_span_event(span, "tool", session_id="test_session_id")
    )


def test_tool_decorator_default_kwargs(LLMObs, mock_llmobs_writer):
    @tool()
    def f():
        pass

    f()
    span = LLMObs._instance.tracer.pop()[0]
    mock_llmobs_writer.enqueue.assert_called_with(_expected_llmobs_non_llm_span_event(span, "tool"))


def test_tool_decorator_gen_default_kwargs(LLMObs, mock_llmobs_writer):
    @tool()
    def f():
        for i in range(10):
            yield i

    for _ in f():
        pass
    span = LLMObs._instance.tracer.pop()[0]
    mock_llmobs_writer.enqueue.assert_called_with(_expected_llmobs_non_llm_span_event(span, "tool"))


def test_workflow_decorator(LLMObs, mock_llmobs_writer):
    @workflow(name="test_function", session_id="test_session_id")
    def f():
        pass

    f()
    span = LLMObs._instance.tracer.pop()[0]
    mock_llmobs_writer.enqueue.assert_called_with(
        _expected_llmobs_non_llm_span_event(span, "workflow", session_id="test_session_id")
    )


def test_workflow_decorator_gen(LLMObs, mock_llmobs_writer):
    @workflow(name="test_function", session_id="test_session_id")
    def f():
        for i in range(10):
            yield i

    for _ in f():
        pass
    span = LLMObs._instance.tracer.pop()[0]
    mock_llmobs_writer.enqueue.assert_called_with(
        _expected_llmobs_non_llm_span_event(span, "workflow", session_id="test_session_id")
    )


def test_workflow_decorator_default_kwargs(LLMObs, mock_llmobs_writer):
    @workflow()
    def f():
        pass

    f()
    span = LLMObs._instance.tracer.pop()[0]
    mock_llmobs_writer.enqueue.assert_called_with(_expected_llmobs_non_llm_span_event(span, "workflow"))


def test_workflow_decorator_gen_default_kwargs(LLMObs, mock_llmobs_writer):
    @workflow()
    def f():
        for i in range(10):
            yield i

    for _ in f():
        pass
    span = LLMObs._instance.tracer.pop()[0]
    mock_llmobs_writer.enqueue.assert_called_with(_expected_llmobs_non_llm_span_event(span, "workflow"))


def test_agent_decorator(LLMObs, mock_llmobs_writer):
    @agent(name="test_function", session_id="test_session_id")
    def f():
        pass

    f()
    span = LLMObs._instance.tracer.pop()[0]
    mock_llmobs_writer.enqueue.assert_called_with(
        _expected_llmobs_llm_span_event(span, "agent", session_id="test_session_id")
    )


def test_agent_decorator_gen(LLMObs, mock_llmobs_writer):
    @agent(name="test_function", session_id="test_session_id")
    def f():
        for i in range(10):
            yield i

    for _ in f():
        pass
    span = LLMObs._instance.tracer.pop()[0]
    mock_llmobs_writer.enqueue.assert_called_with(
        _expected_llmobs_llm_span_event(span, "agent", session_id="test_session_id")
    )


def test_agent_decorator_default_kwargs(LLMObs, mock_llmobs_writer):
    @agent()
    def f():
        pass

    f()
    span = LLMObs._instance.tracer.pop()[0]
    mock_llmobs_writer.enqueue.assert_called_with(_expected_llmobs_llm_span_event(span, "agent"))


def test_agent_decorator_gen_default_kwargs(LLMObs, mock_llmobs_writer):
    @agent()
    def f():
        for i in range(10):
            yield i

    for _ in f():
        pass
    span = LLMObs._instance.tracer.pop()[0]
    mock_llmobs_writer.enqueue.assert_called_with(_expected_llmobs_llm_span_event(span, "agent"))


def test_llm_decorator_with_error(LLMObs, mock_llmobs_writer):
    @llm(model_name="test_model", model_provider="test_provider", name="test_function", session_id="test_session_id")
    def f():
        raise ValueError("test_error")

    with pytest.raises(ValueError):
        f()
    span = LLMObs._instance.tracer.pop()[0]
    mock_llmobs_writer.enqueue.assert_called_with(
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


def test_llm_decorator_gen_with_error(LLMObs, mock_llmobs_writer):
    @llm(model_name="test_model", model_provider="test_provider", name="test_function", session_id="test_session_id")
    def f():
        for i in range(10):
            if i == 5:
                raise ValueError("test_error")
            yield i

    with pytest.raises(ValueError):
        for _ in f():
            pass
    span = LLMObs._instance.tracer.pop()[0]
    mock_llmobs_writer.enqueue.assert_called_with(
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


def test_non_llm_decorators_with_error(LLMObs, mock_llmobs_writer):
    for decorator_name, decorator in [("task", task), ("workflow", workflow), ("tool", tool), ("agent", agent)]:

        @decorator(name="test_function", session_id="test_session_id")
        def f():
            raise ValueError("test_error")

        with pytest.raises(ValueError):
            f()
        span = LLMObs._instance.tracer.pop()[0]
        mock_llmobs_writer.enqueue.assert_called_with(
            _expected_llmobs_non_llm_span_event(
                span,
                decorator_name,
                session_id="test_session_id",
                error=span.get_tag("error.type"),
                error_message=span.get_tag("error.message"),
                error_stack=span.get_tag("error.stack"),
            )
        )


def test_non_llm_decorators_gen_with_error(LLMObs, mock_llmobs_writer):
    for decorator_name, decorator in [("task", task), ("workflow", workflow), ("tool", tool), ("agent", agent)]:

        @decorator(name="test_function", session_id="test_session_id")
        def f():
            for i in range(10):
                if i == 5:
                    raise ValueError("test_error")
                yield i

        with pytest.raises(ValueError):
            for _ in f():
                pass
        span = LLMObs._instance.tracer.pop()[0]
        mock_llmobs_writer.enqueue.assert_called_with(
            _expected_llmobs_non_llm_span_event(
                span,
                decorator_name,
                session_id="test_session_id",
                error=span.get_tag("error.type"),
                error_message=span.get_tag("error.message"),
                error_stack=span.get_tag("error.stack"),
            )
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
        _expected_llmobs_llm_span_event(
            span,
            "llm",
            model_name="test_model",
            model_provider="test_provider",
            input_messages=[{"content": "test_prompt"}],
            output_messages=[{"content": "test_response"}],
            parameters={"temperature": 0.9, "max_tokens": 50},
            token_metrics={"prompt_tokens": 10, "completion_tokens": 20, "total_tokens": 30},
            tags={"custom_tag": "tag_value"},
            session_id="test_session_id",
        )
    )


def test_llm_gen_annotate(LLMObs, mock_llmobs_writer):
    annotate_at = 5
    close_at = annotate_at * 2  # close the generator at this iteration
    total_iters = annotate_at * 4

    @llm(model_name="test_model", model_provider="test_provider", name="test_function", session_id="test_session_id")
    def f():
        count_signals = 0
        try:
            for i in range(total_iters):
                if i == annotate_at:
                    LLMObs.annotate(
                        parameters={"temperature": 0.9, "max_tokens": 50},
                        input_data=[{"content": "test_prompt"}],
                        output_data=[{"content": "test_response"}],
                        metrics={"prompt_tokens": 10, "completion_tokens": 20, "total_tokens": 30},
                    )
                elif i == total_iters - 1:
                    # we should never reach this iteration send .close() is called
                    LLMObs.annotate(tags={"unreached_key": "unreached_val"})
                got = yield i
                if got is not None:
                    count_signals += got
        except GeneratorExit:
            # custom cleanups on generator exit should still work
            count_signals += 1
            LLMObs.annotate(tags={"signals": count_signals})
            raise GeneratorExit

    gen = f()

    for i in gen:
        if i == annotate_at:
            gen.send(1)
        elif i == close_at:
            gen.close()

    span = LLMObs._instance.tracer.pop()[0]
    mock_llmobs_writer.enqueue.assert_called_with(
        _expected_llmobs_llm_span_event(
            span,
            "llm",
            model_name="test_model",
            model_provider="test_provider",
            input_messages=[{"content": "test_prompt"}],
            output_messages=[{"content": "test_response"}],
            parameters={"temperature": 0.9, "max_tokens": 50},
            token_metrics={"prompt_tokens": 10, "completion_tokens": 20, "total_tokens": 30},
            tags={"signals": 2},
            session_id="test_session_id",
        )
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
        _expected_llmobs_llm_span_event(
            span,
            "llm",
            model_name="test_model",
            model_provider="test_provider",
            input_messages=[{"content": "test_prompt"}],
            output_messages=[{"content": "test_response"}],
            parameters={"temperature": 0.9, "max_tokens": 50},
            token_metrics={"prompt_tokens": 10, "completion_tokens": 20, "total_tokens": 30},
            tags={"custom_tag": "tag_value"},
            session_id="test_session_id",
        )
    )


def test_non_llm_decorators_no_args(LLMObs, mock_llmobs_writer):
    """Test that using the decorators without any arguments, i.e. @tool, works the same as @tool(...)."""
    for decorator_name, decorator in [("task", task), ("workflow", workflow), ("tool", tool)]:

        @decorator
        def f():
            pass

        f()
        span = LLMObs._instance.tracer.pop()[0]
        mock_llmobs_writer.enqueue.assert_called_with(_expected_llmobs_non_llm_span_event(span, decorator_name))


def test_agent_decorator_no_args(LLMObs, mock_llmobs_writer):
    """Test that using agent decorator without any arguments, i.e. @agent, works the same as @agent(...)."""

    @agent
    def f():
        pass

    f()
    span = LLMObs._instance.tracer.pop()[0]
    mock_llmobs_writer.enqueue.assert_called_with(_expected_llmobs_llm_span_event(span, "agent"))


def test_non_llm_decorators_gen_no_args(LLMObs, mock_llmobs_writer):
    """Test that using the decorators without any arguments, i.e. @tool, works the same as @tool(...)."""
    for decorator_name, decorator in [("task", task), ("workflow", workflow), ("tool", tool)]:

        @decorator
        def f():
            for i in range(10):
                yield i

        for _ in f():
            pass
        span = LLMObs._instance.tracer.pop()[0]
        mock_llmobs_writer.enqueue.assert_called_with(_expected_llmobs_non_llm_span_event(span, decorator_name))


def test_agent_decorator_gen_no_args(LLMObs, mock_llmobs_writer):
    """Test that using agent decorator without any arguments, i.e. @agent, works the same as @agent(...)."""

    @agent
    def f():
        for i in range(10):
            yield i

    for _ in f():
        pass
    span = LLMObs._instance.tracer.pop()[0]
    mock_llmobs_writer.enqueue.assert_called_with(_expected_llmobs_llm_span_event(span, "agent"))
