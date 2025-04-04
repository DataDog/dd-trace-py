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
from tests.llmobs._utils import _expected_span_link


@pytest.fixture
def mock_logs():
    with mock.patch("ddtrace.llmobs.decorators.log") as mock_logs:
        yield mock_logs


def test_llm_decorator_with_llmobs_disabled_logs_warning(llmobs, mock_logs):
    for decorator_name, decorator in (("llm", llm), ("embedding", embedding)):

        @decorator(
            model_name="test_model", model_provider="test_provider", name="test_function", session_id="test_session_id"
        )
        def f():
            pass

        llmobs.disable()
        f()
        mock_logs.warning.assert_called_with(SPAN_START_WHILE_DISABLED_WARNING)
        mock_logs.reset_mock()


def test_non_llm_decorator_with_llmobs_disabled_logs_warning(llmobs, mock_logs):
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

        llmobs.disable()
        f()
        mock_logs.warning.assert_called_with(SPAN_START_WHILE_DISABLED_WARNING)
        mock_logs.reset_mock()


def test_llm_decorator(llmobs, llmobs_events):
    @llm(model_name="test_model", model_provider="test_provider", name="test_function", session_id="test_session_id")
    def f():
        pass

    f()
    span = llmobs._instance.tracer.pop()[0]
    assert llmobs_events[0] == _expected_llmobs_llm_span_event(
        span, "llm", model_name="test_model", model_provider="test_provider", session_id="test_session_id"
    )


def test_llm_decorator_no_model_name_sets_default(llmobs, llmobs_events):
    @llm(model_provider="test_provider", name="test_function", session_id="test_session_id")
    def f():
        pass

    f()
    span = llmobs._instance.tracer.pop()[0]
    assert llmobs_events[0] == _expected_llmobs_llm_span_event(
        span, "llm", model_name="custom", model_provider="test_provider", session_id="test_session_id"
    )


def test_llm_decorator_default_kwargs(llmobs, llmobs_events):
    @llm
    def f():
        pass

    f()
    span = llmobs._instance.tracer.pop()[0]
    assert llmobs_events[0] == _expected_llmobs_llm_span_event(
        span, "llm", model_name="custom", model_provider="custom"
    )


def test_embedding_decorator(llmobs, llmobs_events):
    @embedding(
        model_name="test_model", model_provider="test_provider", name="test_function", session_id="test_session_id"
    )
    def f():
        pass

    f()
    span = llmobs._instance.tracer.pop()[0]
    assert llmobs_events[0] == _expected_llmobs_llm_span_event(
        span, "embedding", model_name="test_model", model_provider="test_provider", session_id="test_session_id"
    )


def test_embedding_decorator_no_model_name_sets_default(llmobs, llmobs_events):
    @embedding(model_provider="test_provider", name="test_function", session_id="test_session_id")
    def f():
        pass

    f()
    span = llmobs._instance.tracer.pop()[0]
    assert llmobs_events[0] == _expected_llmobs_llm_span_event(
        span, "embedding", model_name="custom", model_provider="test_provider", session_id="test_session_id"
    )


def test_embedding_decorator_default_kwargs(llmobs, llmobs_events):
    @embedding
    def f():
        pass

    f()
    span = llmobs._instance.tracer.pop()[0]
    assert llmobs_events[0] == _expected_llmobs_llm_span_event(
        span, "embedding", model_name="custom", model_provider="custom"
    )


def test_retrieval_decorator(llmobs, llmobs_events):
    @retrieval(name="test_function", session_id="test_session_id")
    def f():
        pass

    f()
    span = llmobs._instance.tracer.pop()[0]
    assert llmobs_events[0] == _expected_llmobs_non_llm_span_event(span, "retrieval", session_id="test_session_id")


def test_retrieval_decorator_default_kwargs(llmobs, llmobs_events):
    @retrieval()
    def f():
        pass

    f()
    span = llmobs._instance.tracer.pop()[0]
    assert llmobs_events[0] == _expected_llmobs_non_llm_span_event(span, "retrieval")


def test_task_decorator(llmobs, llmobs_events):
    @task(name="test_function", session_id="test_session_id")
    def f():
        pass

    f()
    span = llmobs._instance.tracer.pop()[0]
    assert llmobs_events[0] == _expected_llmobs_non_llm_span_event(span, "task", session_id="test_session_id")


def test_task_decorator_default_kwargs(llmobs, llmobs_events):
    @task()
    def f():
        pass

    f()
    span = llmobs._instance.tracer.pop()[0]
    assert llmobs_events[0] == _expected_llmobs_non_llm_span_event(span, "task")


def test_tool_decorator(llmobs, llmobs_events):
    @tool(name="test_function", session_id="test_session_id")
    def f():
        pass

    f()
    span = llmobs._instance.tracer.pop()[0]
    assert llmobs_events[0] == _expected_llmobs_non_llm_span_event(span, "tool", session_id="test_session_id")


def test_tool_decorator_default_kwargs(llmobs, llmobs_events):
    @tool()
    def f():
        pass

    f()
    span = llmobs._instance.tracer.pop()[0]
    assert llmobs_events[0] == _expected_llmobs_non_llm_span_event(span, "tool")


def test_workflow_decorator(llmobs, llmobs_events):
    @workflow(name="test_function", session_id="test_session_id")
    def f():
        pass

    f()
    span = llmobs._instance.tracer.pop()[0]
    assert llmobs_events[0] == _expected_llmobs_non_llm_span_event(span, "workflow", session_id="test_session_id")


def test_workflow_decorator_default_kwargs(llmobs, llmobs_events):
    @workflow()
    def f():
        pass

    f()
    span = llmobs._instance.tracer.pop()[0]
    assert llmobs_events[0] == _expected_llmobs_non_llm_span_event(span, "workflow")


def test_agent_decorator(llmobs, llmobs_events):
    @agent(name="test_function", session_id="test_session_id")
    def f():
        pass

    f()
    span = llmobs._instance.tracer.pop()[0]
    assert llmobs_events[0] == _expected_llmobs_llm_span_event(span, "agent", session_id="test_session_id")


def test_agent_decorator_default_kwargs(llmobs, llmobs_events):
    @agent()
    def f():
        pass

    f()
    span = llmobs._instance.tracer.pop()[0]
    assert llmobs_events[0] == _expected_llmobs_llm_span_event(span, "agent")


def test_llm_decorator_with_error(llmobs, llmobs_events):
    @llm(model_name="test_model", model_provider="test_provider", name="test_function", session_id="test_session_id")
    def f():
        raise ValueError("test_error")

    with pytest.raises(ValueError):
        f()
    span = llmobs._instance.tracer.pop()[0]
    assert llmobs_events[0] == _expected_llmobs_llm_span_event(
        span,
        "llm",
        model_name="test_model",
        model_provider="test_provider",
        session_id="test_session_id",
        error=span.get_tag("error.type"),
        error_message=span.get_tag("error.message"),
        error_stack=span.get_tag("error.stack"),
    )


def test_non_llm_decorators_with_error(llmobs, llmobs_events):
    for decorator_name, decorator in [("task", task), ("workflow", workflow), ("tool", tool), ("agent", agent)]:

        @decorator(name="test_function", session_id="test_session_id")
        def f():
            raise ValueError("test_error")

        with pytest.raises(ValueError):
            f()
        span = llmobs._instance.tracer.pop()[0]
        assert llmobs_events[-1] == _expected_llmobs_non_llm_span_event(
            span,
            decorator_name,
            session_id="test_session_id",
            error=span.get_tag("error.type"),
            error_message=span.get_tag("error.message"),
            error_stack=span.get_tag("error.stack"),
        )


def test_llm_annotate(llmobs, llmobs_events):
    @llm(model_name="test_model", model_provider="test_provider", name="test_function", session_id="test_session_id")
    def f():
        llmobs.annotate(
            metadata={"temperature": 0.9, "max_tokens": 50},
            input_data=[{"content": "test_prompt"}],
            output_data=[{"content": "test_response"}],
            tags={"custom_tag": "tag_value"},
            metrics={"input_tokens": 10, "output_tokens": 20, "total_tokens": 30},
        )

    f()
    span = llmobs._instance.tracer.pop()[0]
    assert llmobs_events[0] == _expected_llmobs_llm_span_event(
        span,
        "llm",
        model_name="test_model",
        model_provider="test_provider",
        input_messages=[{"content": "test_prompt"}],
        output_messages=[{"content": "test_response"}],
        metadata={"temperature": 0.9, "max_tokens": 50},
        token_metrics={"input_tokens": 10, "output_tokens": 20, "total_tokens": 30},
        tags={"custom_tag": "tag_value"},
        session_id="test_session_id",
    )


def test_llm_annotate_raw_string_io(llmobs, llmobs_events):
    @llm(model_name="test_model", model_provider="test_provider", name="test_function", session_id="test_session_id")
    def f():
        llmobs.annotate(
            metadata={"temperature": 0.9, "max_tokens": 50},
            input_data="test_prompt",
            output_data="test_response",
            tags={"custom_tag": "tag_value"},
            metrics={"input_tokens": 10, "output_tokens": 20, "total_tokens": 30},
        )

    f()
    span = llmobs._instance.tracer.pop()[0]
    assert llmobs_events[0] == _expected_llmobs_llm_span_event(
        span,
        "llm",
        model_name="test_model",
        model_provider="test_provider",
        input_messages=[{"content": "test_prompt"}],
        output_messages=[{"content": "test_response"}],
        metadata={"temperature": 0.9, "max_tokens": 50},
        token_metrics={"input_tokens": 10, "output_tokens": 20, "total_tokens": 30},
        tags={"custom_tag": "tag_value"},
        session_id="test_session_id",
    )


def test_non_llm_decorators_no_args(llmobs, llmobs_events):
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
        span = llmobs._instance.tracer.pop()[0]
        assert llmobs_events[-1] == _expected_llmobs_non_llm_span_event(span, decorator_name)


def test_agent_decorator_no_args(llmobs, llmobs_events):
    """Test that using agent decorator without any arguments, i.e. @agent, works the same as @agent(...)."""

    @agent
    def f():
        pass

    f()
    span = llmobs._instance.tracer.pop()[0]
    assert llmobs_events[0] == _expected_llmobs_llm_span_event(span, "agent")


def test_ml_app_override(llmobs, llmobs_events):
    """Test that setting ml_app kwarg on the LLMObs decorators will override the DD_LLMOBS_ML_APP value."""
    for decorator_name, decorator in [("task", task), ("workflow", workflow), ("tool", tool)]:

        @decorator(ml_app="test_ml_app")
        def f():
            pass

        f()
        span = llmobs._instance.tracer.pop()[0]
        assert llmobs_events[-1] == _expected_llmobs_non_llm_span_event(
            span, decorator_name, tags={"ml_app": "test_ml_app"}
        )

    @llm(model_name="test_model", ml_app="test_ml_app")
    def g():
        pass

    g()
    span = llmobs._instance.tracer.pop()[0]
    assert llmobs_events[-1] == _expected_llmobs_llm_span_event(
        span, "llm", model_name="test_model", model_provider="custom", tags={"ml_app": "test_ml_app"}
    )

    @embedding(model_name="test_model", ml_app="test_ml_app")
    def h():
        pass

    h()
    span = llmobs._instance.tracer.pop()[0]
    assert llmobs_events[-1] == _expected_llmobs_llm_span_event(
        span, "embedding", model_name="test_model", model_provider="custom", tags={"ml_app": "test_ml_app"}
    )


async def test_non_llm_async_decorators(llmobs, llmobs_events):
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
        span = llmobs._instance.tracer.pop()[0]
        assert llmobs_events[-1] == _expected_llmobs_non_llm_span_event(span, decorator_name)


async def test_llm_async_decorators(llmobs, llmobs_events):
    """Test that decorators work with async functions."""
    for decorator_name, decorator in [("llm", llm), ("embedding", embedding)]:

        @decorator(model_name="test_model", model_provider="test_provider")
        async def f():
            pass

        await f()
        span = llmobs._instance.tracer.pop()[0]
        assert llmobs_events[-1] == _expected_llmobs_llm_span_event(
            span, decorator_name, model_name="test_model", model_provider="test_provider"
        )


def test_automatic_annotation_non_llm_decorators(llmobs, llmobs_events):
    """Test that automatic input/output annotation works for non-LLM decorators."""
    for decorator_name, decorator in (("task", task), ("workflow", workflow), ("tool", tool), ("agent", agent)):

        @decorator(name="test_function", session_id="test_session_id")
        def f(prompt, arg_2, kwarg_1=None, kwarg_2=None):
            return prompt

        f("test_prompt", "arg_2", kwarg_2=12345)
        span = llmobs._instance.tracer.pop()[0]
        assert llmobs_events[-1] == _expected_llmobs_non_llm_span_event(
            span,
            decorator_name,
            input_value='{"prompt": "test_prompt", "arg_2": "arg_2", "kwarg_2": 12345}',
            output_value="test_prompt",
            session_id="test_session_id",
        )


def test_automatic_annotation_retrieval_decorator(llmobs, llmobs_events):
    """Test that automatic input annotation works for retrieval decorators."""

    @retrieval(session_id="test_session_id")
    def test_retrieval(query, arg_2, kwarg_1=None, kwarg_2=None):
        return [{"name": "name", "id": "1234567890", "score": 0.9}]

    test_retrieval("test_query", "arg_2", kwarg_2=12345)
    span = llmobs._instance.tracer.pop()[0]
    assert llmobs_events[0] == _expected_llmobs_non_llm_span_event(
        span,
        "retrieval",
        input_value='{"query": "test_query", "arg_2": "arg_2", "kwarg_2": 12345}',
        session_id="test_session_id",
    )


def test_automatic_annotation_off_non_llm_decorators(llmobs, llmobs_events):
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
        span = llmobs._instance.tracer.pop()[0]
        assert llmobs_events[-1] == _expected_llmobs_non_llm_span_event(
            span, decorator_name, session_id="test_session_id"
        )


def test_automatic_annotation_off_if_manually_annotated(llmobs, llmobs_events):
    """Test disabling automatic input/output annotation for non-LLM decorators."""
    for decorator_name, decorator in (("task", task), ("workflow", workflow), ("tool", tool), ("agent", agent)):

        @decorator(name="test_function", session_id="test_session_id")
        def f(prompt, arg_2, kwarg_1=None, kwarg_2=None):
            llmobs.annotate(input_data="my custom input", output_data="my custom output")
            return prompt

        f("test_prompt", "arg_2", kwarg_2=12345)
        span = llmobs._instance.tracer.pop()[0]
        assert llmobs_events[-1] == _expected_llmobs_non_llm_span_event(
            span,
            decorator_name,
            session_id="test_session_id",
            input_value="my custom input",
            output_value="my custom output",
        )


def test_generator_sync(llmobs, llmobs_events):
    """
    Test that decorators work with generator functions.
    The span should finish after the generator is exhausted.
    """
    for decorator_name, decorator in (
        ("task", task),
        ("workflow", workflow),
        ("tool", tool),
        ("agent", agent),
        ("retrieval", retrieval),
        ("llm", llm),
        ("embedding", embedding),
    ):

        @decorator()
        def f():
            for i in range(3):
                yield i

            llmobs.annotate(
                input_data="hello",
                output_data="world",
            )

        i = 0
        for e in f():
            assert e == i
            i += 1

        span = llmobs._instance.tracer.pop()[0]
        if decorator_name == "llm":
            expected_span_event = _expected_llmobs_llm_span_event(
                span,
                decorator_name,
                input_messages=[{"content": "hello"}],
                output_messages=[{"content": "world"}],
                model_name="custom",
                model_provider="custom",
            )
        elif decorator_name == "embedding":
            expected_span_event = _expected_llmobs_llm_span_event(
                span,
                decorator_name,
                input_documents=[{"text": "hello"}],
                output_value="world",
                model_name="custom",
                model_provider="custom",
            )
        elif decorator_name == "retrieval":
            expected_span_event = _expected_llmobs_non_llm_span_event(
                span, decorator_name, input_value="hello", output_documents=[{"text": "world"}]
            )
        else:
            expected_span_event = _expected_llmobs_non_llm_span_event(
                span, decorator_name, input_value="hello", output_value="world"
            )

        assert llmobs_events[-1] == expected_span_event


async def test_generator_async(llmobs, llmobs_events):
    """
    Test that decorators work with generator functions.
    The span should finish after the generator is exhausted.
    """
    for decorator_name, decorator in (
        ("task", task),
        ("workflow", workflow),
        ("tool", tool),
        ("agent", agent),
        ("retrieval", retrieval),
        ("llm", llm),
        ("embedding", embedding),
    ):

        @decorator()
        async def f():
            for i in range(3):
                yield i

            llmobs.annotate(
                input_data="hello",
                output_data="world",
            )

        i = 0
        async for e in f():
            assert e == i
            i += 1

        span = llmobs._instance.tracer.pop()[0]
        if decorator_name == "llm":
            expected_span_event = _expected_llmobs_llm_span_event(
                span,
                decorator_name,
                input_messages=[{"content": "hello"}],
                output_messages=[{"content": "world"}],
                model_name="custom",
                model_provider="custom",
            )
        elif decorator_name == "embedding":
            expected_span_event = _expected_llmobs_llm_span_event(
                span,
                decorator_name,
                input_documents=[{"text": "hello"}],
                output_value="world",
                model_name="custom",
                model_provider="custom",
            )
        elif decorator_name == "retrieval":
            expected_span_event = _expected_llmobs_non_llm_span_event(
                span, decorator_name, input_value="hello", output_documents=[{"text": "world"}]
            )
        else:
            expected_span_event = _expected_llmobs_non_llm_span_event(
                span, decorator_name, input_value="hello", output_value="world"
            )

        assert llmobs_events[-1] == expected_span_event


def test_generator_sync_with_llmobs_disabled(llmobs, mock_logs):
    llmobs.disable()

    @workflow()
    def f():
        for i in range(3):
            yield i

    i = 0
    for e in f():
        assert e == i
        i += 1

    mock_logs.warning.assert_called_with(SPAN_START_WHILE_DISABLED_WARNING)

    @llm()
    def g():
        for i in range(3):
            yield i

    i = 0
    for e in g():
        assert e == i
        i += 1

    mock_logs.warning.assert_called_with(SPAN_START_WHILE_DISABLED_WARNING)
    llmobs.enable()


async def test_generator_async_with_llmobs_disabled(llmobs, mock_logs):
    llmobs.disable()

    @workflow()
    async def f():
        for i in range(3):
            yield i

    i = 0
    async for e in f():
        assert e == i
        i += 1

    mock_logs.warning.assert_called_with(SPAN_START_WHILE_DISABLED_WARNING)

    @llm()
    async def g():
        for i in range(3):
            yield i

    i = 0
    async for e in g():
        assert e == i
        i += 1

    mock_logs.warning.assert_called_with(SPAN_START_WHILE_DISABLED_WARNING)
    llmobs.enable()


def test_generator_sync_finishes_span_on_error(llmobs, llmobs_events):
    """Tests that"""

    @workflow()
    def f():
        for i in range(3):
            if i == 1:
                raise ValueError("test_error")
            yield i

    with pytest.raises(ValueError):
        for _ in f():
            pass

    span = llmobs._instance.tracer.pop()[0]
    assert llmobs_events[0] == _expected_llmobs_non_llm_span_event(
        span,
        "workflow",
        error=span.get_tag("error.type"),
        error_message=span.get_tag("error.message"),
        error_stack=span.get_tag("error.stack"),
    )


async def test_generator_async_finishes_span_on_error(llmobs, llmobs_events):
    @workflow()
    async def f():
        for i in range(3):
            if i == 1:
                raise ValueError("test_error")
            yield i

    with pytest.raises(ValueError):
        async for _ in f():
            pass

    span = llmobs._instance.tracer.pop()[0]
    assert llmobs_events[0] == _expected_llmobs_non_llm_span_event(
        span,
        "workflow",
        error=span.get_tag("error.type"),
        error_message=span.get_tag("error.message"),
        error_stack=span.get_tag("error.stack"),
    )


def test_generator_sync_send(llmobs, llmobs_events):
    @workflow()
    def f():
        while True:
            i = yield
            yield i**2

    gen = f()
    next(gen)
    assert gen.send(2) == 4
    next(gen)
    assert gen.send(3) == 9
    next(gen)
    assert gen.send(4) == 16
    gen.close()

    span = llmobs._instance.tracer.pop()[0]
    assert llmobs_events[0] == _expected_llmobs_non_llm_span_event(span, "workflow")


async def test_generator_async_send(llmobs, llmobs_events):
    @workflow()
    async def f():
        while True:
            value = yield
            yield value**2

    gen = f()
    await gen.asend(None)  # Prime the generator

    for i in range(5):
        assert (await gen.asend(i)) == i**2
        await gen.asend(None)

    await gen.aclose()

    span = llmobs._instance.tracer.pop()[0]
    assert llmobs_events[0] == _expected_llmobs_non_llm_span_event(span, "workflow")


def test_generator_sync_throw(llmobs, llmobs_events):
    @workflow()
    def f():
        for i in range(3):
            yield i

    with pytest.raises(ValueError):
        gen = f()
        next(gen)
        gen.throw(ValueError("test_error"))

    span = llmobs._instance.tracer.pop()[0]
    assert llmobs_events[0] == _expected_llmobs_non_llm_span_event(
        span,
        "workflow",
        error=span.get_tag("error.type"),
        error_message=span.get_tag("error.message"),
        error_stack=span.get_tag("error.stack"),
    )


async def test_generator_async_throw(llmobs, llmobs_events):
    @workflow()
    async def f():
        for i in range(3):
            yield i

    with pytest.raises(ValueError):
        gen = f()
        await gen.asend(None)
        await gen.athrow(ValueError("test_error"))

    span = llmobs._instance.tracer.pop()[0]
    assert llmobs_events[0] == _expected_llmobs_non_llm_span_event(
        span,
        "workflow",
        error=span.get_tag("error.type"),
        error_message=span.get_tag("error.message"),
        error_stack=span.get_tag("error.stack"),
    )


def test_generator_exit_exception_sync(llmobs, llmobs_events):
    @workflow()
    def get_next_element(alist):
        for element in alist:
            try:
                yield element
            except BaseException:  # except Exception
                pass

    for element in get_next_element([0, 1, 2, 3, 4, 5, 6, 7, 8, 9]):
        if element == 5:
            break

    span = llmobs._instance.tracer.pop()[0]
    assert llmobs_events[0] == _expected_llmobs_non_llm_span_event(
        span,
        "workflow",
        input_value='{"alist": [0, 1, 2, 3, 4, 5, 6, 7, 8, 9]}',
        error=span.get_tag("error.type"),
        error_message=span.get_tag("error.message"),
        error_stack=span.get_tag("error.stack"),
    )


def test_decorator_records_span_links(llmobs, llmobs_events):
    @workflow
    def one(inp):
        return 1

    @task
    def two(inp):
        return inp

    with llmobs.agent("dummy_trace"):
        two(one("test_input"))

    one_span = llmobs_events[0]
    two_span = llmobs_events[1]

    assert "span_links" not in one_span
    assert len(two_span["span_links"]) == 2
    assert two_span["span_links"][0] == _expected_span_link(one_span, "output", "input")
    assert two_span["span_links"][1] == _expected_span_link(one_span, "output", "output")


def test_decorator_records_span_links_for_multi_input_functions(llmobs, llmobs_events):
    @agent
    def some_agent(a, b):
        pass

    @workflow
    def one():
        return 1

    @task
    def two():
        return 2

    with llmobs.agent("dummy_trace"):
        some_agent(one(), two())

    one_span = llmobs_events[0]
    two_span = llmobs_events[1]
    three_span = llmobs_events[2]

    assert "span_links" not in one_span
    assert "span_links" not in two_span
    assert len(three_span["span_links"]) == 2
    assert three_span["span_links"][0] == _expected_span_link(one_span, "output", "input")
    assert three_span["span_links"][1] == _expected_span_link(two_span, "output", "input")


def test_decorator_records_span_links_via_kwargs(llmobs, llmobs_events):
    @agent
    def some_agent(a=None, b=None):
        pass

    @workflow
    def one():
        return 1

    @task
    def two():
        return 2

    with llmobs.agent("dummy_trace"):
        some_agent(one(), two())

    one_span = llmobs_events[0]
    two_span = llmobs_events[1]
    three_span = llmobs_events[2]

    assert "span_links" not in one_span
    assert "span_links" not in two_span
    assert len(three_span["span_links"]) == 2
    assert three_span["span_links"][0] == _expected_span_link(one_span, "output", "input")
    assert three_span["span_links"][1] == _expected_span_link(two_span, "output", "input")
