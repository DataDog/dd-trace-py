import json

import mock
import pytest

from ddtrace.llmobs._constants import SPAN_START_WHILE_DISABLED_WARNING
from ddtrace.llmobs._constants import UNKNOWN_MODEL_NAME
from ddtrace.llmobs._constants import UNKNOWN_MODEL_PROVIDER
from ddtrace.llmobs._utils import _get_llmobs_data_metastruct
from ddtrace.llmobs._utils import get_llmobs_input_value
from ddtrace.llmobs._utils import get_llmobs_output
from ddtrace.llmobs._utils import get_llmobs_span_kind
from ddtrace.llmobs.decorators import agent
from ddtrace.llmobs.decorators import embedding
from ddtrace.llmobs.decorators import llm
from ddtrace.llmobs.decorators import retrieval
from ddtrace.llmobs.decorators import task
from ddtrace.llmobs.decorators import tool
from ddtrace.llmobs.decorators import workflow
from tests.llmobs._utils import assert_llmobs_span_data


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


def test_llm_decorator(llmobs, test_spans):
    @llm(model_name="test_model", model_provider="test_provider", name="test_function", session_id="test_session_id")
    def f():
        pass

    f()
    span = test_spans.pop()[0]
    assert_llmobs_span_data(
        _get_llmobs_data_metastruct(span),
        span_kind="llm",
        model_name="test_model",
        model_provider="test_provider",
        tags={"session_id": "test_session_id", "decorator": "1"},
    )


def test_llm_decorator_no_model_name_sets_default(llmobs, test_spans):
    @llm(model_provider="test_provider", name="test_function", session_id="test_session_id")
    def f():
        pass

    f()
    span = test_spans.pop()[0]
    assert_llmobs_span_data(
        _get_llmobs_data_metastruct(span),
        span_kind="llm",
        model_name=UNKNOWN_MODEL_NAME,
        model_provider="test_provider",
        tags={"session_id": "test_session_id", "decorator": "1"},
    )


def test_llm_decorator_default_kwargs(llmobs, test_spans):
    @llm
    def f():
        pass

    f()
    span = test_spans.pop()[0]
    assert_llmobs_span_data(
        _get_llmobs_data_metastruct(span),
        span_kind="llm",
        model_name=UNKNOWN_MODEL_NAME,
        model_provider=UNKNOWN_MODEL_PROVIDER,
        tags={"decorator": "1"},
    )


def test_embedding_decorator(llmobs, test_spans):
    @embedding(
        model_name="test_model", model_provider="test_provider", name="test_function", session_id="test_session_id"
    )
    def f():
        pass

    f()
    span = test_spans.pop()[0]
    assert_llmobs_span_data(
        _get_llmobs_data_metastruct(span),
        span_kind="embedding",
        model_name="test_model",
        model_provider="test_provider",
        tags={"session_id": "test_session_id", "decorator": "1"},
    )


def test_embedding_decorator_no_model_name_sets_default(llmobs, test_spans):
    @embedding(model_provider="test_provider", name="test_function", session_id="test_session_id")
    def f():
        pass

    f()
    span = test_spans.pop()[0]
    assert_llmobs_span_data(
        _get_llmobs_data_metastruct(span),
        span_kind="embedding",
        model_name=UNKNOWN_MODEL_NAME,
        model_provider="test_provider",
        tags={"session_id": "test_session_id", "decorator": "1"},
    )


def test_embedding_decorator_default_kwargs(llmobs, test_spans):
    @embedding
    def f():
        pass

    f()
    span = test_spans.pop()[0]
    assert_llmobs_span_data(
        _get_llmobs_data_metastruct(span),
        span_kind="embedding",
        model_name=UNKNOWN_MODEL_NAME,
        model_provider=UNKNOWN_MODEL_PROVIDER,
        tags={"decorator": "1"},
    )


def test_retrieval_decorator(llmobs, test_spans):
    @retrieval(name="test_function", session_id="test_session_id")
    def f():
        pass

    f()
    span = test_spans.pop()[0]
    assert_llmobs_span_data(
        _get_llmobs_data_metastruct(span),
        span_kind="retrieval",
        tags={"session_id": "test_session_id", "decorator": "1"},
    )


def test_retrieval_decorator_default_kwargs(llmobs, test_spans):
    @retrieval()
    def f():
        pass

    f()
    span = test_spans.pop()[0]
    assert_llmobs_span_data(
        _get_llmobs_data_metastruct(span),
        span_kind="retrieval",
        tags={"decorator": "1"},
    )


def test_task_decorator(llmobs, test_spans):
    @task(name="test_function", session_id="test_session_id")
    def f():
        pass

    f()
    span = test_spans.pop()[0]
    assert_llmobs_span_data(
        _get_llmobs_data_metastruct(span),
        span_kind="task",
        tags={"session_id": "test_session_id", "decorator": "1"},
    )


def test_task_decorator_default_kwargs(llmobs, test_spans):
    @task()
    def f():
        pass

    f()
    span = test_spans.pop()[0]
    assert_llmobs_span_data(
        _get_llmobs_data_metastruct(span),
        span_kind="task",
        tags={"decorator": "1"},
    )


def test_tool_decorator(llmobs, test_spans):
    @tool(name="test_function", session_id="test_session_id")
    def f():
        pass

    f()
    span = test_spans.pop()[0]
    assert_llmobs_span_data(
        _get_llmobs_data_metastruct(span),
        span_kind="tool",
        tags={"session_id": "test_session_id", "decorator": "1"},
    )


def test_tool_decorator_default_kwargs(llmobs, test_spans):
    @tool()
    def f():
        pass

    f()
    span = test_spans.pop()[0]
    assert_llmobs_span_data(
        _get_llmobs_data_metastruct(span),
        span_kind="tool",
        tags={"decorator": "1"},
    )


def test_workflow_decorator(llmobs, test_spans):
    @workflow(name="test_function", session_id="test_session_id")
    def f():
        pass

    f()
    span = test_spans.pop()[0]
    assert_llmobs_span_data(
        _get_llmobs_data_metastruct(span),
        span_kind="workflow",
        tags={"session_id": "test_session_id", "decorator": "1"},
    )


def test_workflow_decorator_default_kwargs(llmobs, test_spans):
    @workflow()
    def f():
        pass

    f()
    span = test_spans.pop()[0]
    assert_llmobs_span_data(
        _get_llmobs_data_metastruct(span),
        span_kind="workflow",
        tags={"decorator": "1"},
    )


def test_agent_decorator(llmobs, test_spans):
    @agent(name="test_function", session_id="test_session_id")
    def f():
        pass

    f()
    span = test_spans.pop()[0]
    assert_llmobs_span_data(
        _get_llmobs_data_metastruct(span),
        span_kind="agent",
        tags={"session_id": "test_session_id", "decorator": "1"},
    )


def test_agent_decorator_default_kwargs(llmobs, test_spans):
    @agent()
    def f():
        pass

    f()
    span = test_spans.pop()[0]
    assert_llmobs_span_data(
        _get_llmobs_data_metastruct(span),
        span_kind="agent",
        tags={"decorator": "1"},
    )


def test_llm_decorator_with_error(llmobs, test_spans):
    @llm(model_name="test_model", model_provider="test_provider", name="test_function", session_id="test_session_id")
    def f():
        raise ValueError("test_error")

    with pytest.raises(ValueError):
        f()
    span = test_spans.pop()[0]
    assert_llmobs_span_data(
        _get_llmobs_data_metastruct(span),
        span_kind="llm",
        model_name="test_model",
        model_provider="test_provider",
        error={
            "type": span.get_tag("error.type"),
            "message": span.get_tag("error.message"),
            "stack": span.get_tag("error.stack"),
        },
        tags={"session_id": "test_session_id", "decorator": "1"},
    )


def test_non_llm_decorators_with_error(llmobs, test_spans):
    for decorator_name, decorator in [("task", task), ("workflow", workflow), ("tool", tool), ("agent", agent)]:

        @decorator(name="test_function", session_id="test_session_id")
        def f():
            raise ValueError("test_error")

        with pytest.raises(ValueError):
            f()
        span = test_spans.pop()[0]
        assert_llmobs_span_data(
            _get_llmobs_data_metastruct(span),
            span_kind=decorator_name,
            error={
                "type": span.get_tag("error.type"),
                "message": span.get_tag("error.message"),
                "stack": span.get_tag("error.stack"),
            },
            tags={"session_id": "test_session_id", "decorator": "1"},
        )


def test_llm_decorator_automatic_output_annotation(llmobs, test_spans):
    """Test that the @llm decorator automatically annotates the return value as output."""

    @llm(model_name="test_model", model_provider="test_provider", name="test_function")
    def f():
        return "test_response"

    f()
    span = test_spans.pop()[0]
    assert_llmobs_span_data(
        _get_llmobs_data_metastruct(span),
        span_kind="llm",
        model_name="test_model",
        model_provider="test_provider",
        output_messages=[{"content": "test_response", "role": ""}],
        tags={"decorator": "1"},
    )


async def test_llm_decorator_automatic_output_annotation_async(llmobs, test_spans):
    """Test that the @llm decorator automatically annotates the return value as output for async functions."""

    @llm(model_name="test_model", model_provider="test_provider", name="test_function")
    async def f():
        return "test_response"

    await f()
    span = test_spans.pop()[0]
    assert_llmobs_span_data(
        _get_llmobs_data_metastruct(span),
        span_kind="llm",
        model_name="test_model",
        model_provider="test_provider",
        output_messages=[{"content": "test_response", "role": ""}],
        tags={"decorator": "1"},
    )


def test_llm_decorator_unparseable_output_logs_warning_not_raises(llmobs, mock_logs, test_spans):
    """Test that @llm decorator does not raise when return value cannot be parsed as messages."""

    @llm(model_name="test_model", model_provider="test_provider", name="test_function")
    def f():
        return 42  # int cannot be parsed as LLM messages

    f()  # should not raise LLMObsAnnotateSpanError
    mock_logs.debug.assert_called_once_with(
        "Failed to auto-annotate output for @%s decorated function. "
        "Use LLMObs.annotate() to manually annotate the output.",
        "llm",
    )
    span = test_spans.pop()[0]
    # span is still created, output messages are not set
    assert get_llmobs_output(span) == {}


async def test_llm_decorator_unparseable_output_logs_warning_not_raises_async(llmobs, mock_logs, test_spans):
    """Test that async @llm decorator does not raise when return value cannot be parsed as messages."""

    @llm(model_name="test_model", model_provider="test_provider", name="test_function")
    async def f():
        return 42  # int cannot be parsed as LLM messages

    await f()  # should not raise LLMObsAnnotateSpanError
    mock_logs.debug.assert_called_once_with(
        "Failed to auto-annotate output for @%s decorated function. "
        "Use LLMObs.annotate() to manually annotate the output.",
        "llm",
    )
    span = test_spans.pop()[0]
    assert get_llmobs_output(span) == {}


def test_llm_decorator_manual_annotation_not_overridden(llmobs, test_spans):
    """Test that manual LLMObs.annotate() is not overridden by automatic output annotation."""

    @llm(model_name="test_model", model_provider="test_provider", name="test_function")
    def f():
        llmobs.annotate(output_data=[{"content": "manual_response"}])
        return "auto_response"

    f()
    span = test_spans.pop()[0]
    assert_llmobs_span_data(
        _get_llmobs_data_metastruct(span),
        span_kind="llm",
        model_name="test_model",
        model_provider="test_provider",
        output_messages=[{"content": "manual_response", "role": ""}],
        tags={"decorator": "1"},
    )


def test_llm_annotate(llmobs, test_spans):
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
    span = test_spans.pop()[0]
    assert_llmobs_span_data(
        _get_llmobs_data_metastruct(span),
        span_kind="llm",
        model_name="test_model",
        model_provider="test_provider",
        input_messages=[{"content": "test_prompt"}],
        output_messages=[{"content": "test_response"}],
        metadata={"temperature": 0.9, "max_tokens": 50},
        metrics={"input_tokens": 10, "output_tokens": 20, "total_tokens": 30},
        tags={"custom_tag": "tag_value", "session_id": "test_session_id", "decorator": "1"},
    )


def test_llm_annotate_raw_string_io(llmobs, test_spans):
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
    span = test_spans.pop()[0]
    assert_llmobs_span_data(
        _get_llmobs_data_metastruct(span),
        span_kind="llm",
        model_name="test_model",
        model_provider="test_provider",
        input_messages=[{"content": "test_prompt"}],
        output_messages=[{"content": "test_response"}],
        metadata={"temperature": 0.9, "max_tokens": 50},
        metrics={"input_tokens": 10, "output_tokens": 20, "total_tokens": 30},
        tags={"custom_tag": "tag_value", "session_id": "test_session_id", "decorator": "1"},
    )


def test_non_llm_decorators_no_args(llmobs, test_spans):
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
        span = test_spans.pop()[0]
        assert_llmobs_span_data(
            _get_llmobs_data_metastruct(span),
            span_kind=decorator_name,
            tags={"decorator": "1"},
        )


def test_agent_decorator_no_args(llmobs, test_spans):
    """Test that using agent decorator without any arguments, i.e. @agent, works the same as @agent(...)."""

    @agent
    def f():
        pass

    f()
    span = test_spans.pop()[0]
    assert_llmobs_span_data(
        _get_llmobs_data_metastruct(span),
        span_kind="agent",
        tags={"decorator": "1"},
    )


def test_ml_app_override(llmobs, test_spans):
    """Test that setting ml_app kwarg on the LLMObs decorators will override the DD_LLMOBS_ML_APP value."""
    for decorator_name, decorator in [("task", task), ("workflow", workflow), ("tool", tool)]:

        @decorator(ml_app="test_ml_app")
        def f():
            pass

        f()
        span = test_spans.pop()[0]
        assert_llmobs_span_data(
            _get_llmobs_data_metastruct(span),
            span_kind=decorator_name,
            tags={"ml_app": "test_ml_app", "decorator": "1"},
        )

    @llm(model_name="test_model", ml_app="test_ml_app")
    def g():
        pass

    g()
    span = test_spans.pop()[0]
    assert_llmobs_span_data(
        _get_llmobs_data_metastruct(span),
        span_kind="llm",
        model_name="test_model",
        model_provider=UNKNOWN_MODEL_PROVIDER,
        tags={"ml_app": "test_ml_app", "decorator": "1"},
    )

    @embedding(model_name="test_model", ml_app="test_ml_app")
    def h():
        pass

    h()
    span = test_spans.pop()[0]
    assert_llmobs_span_data(
        _get_llmobs_data_metastruct(span),
        span_kind="embedding",
        model_name="test_model",
        model_provider=UNKNOWN_MODEL_PROVIDER,
        tags={"ml_app": "test_ml_app", "decorator": "1"},
    )


async def test_non_llm_async_decorators(llmobs, test_spans):
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
        span = test_spans.pop()[0]
        assert_llmobs_span_data(
            _get_llmobs_data_metastruct(span),
            span_kind=decorator_name,
            tags={"decorator": "1"},
        )


async def test_llm_async_decorators(llmobs, test_spans):
    """Test that decorators work with async functions."""
    for decorator_name, decorator in [("llm", llm), ("embedding", embedding)]:

        @decorator(model_name="test_model", model_provider="test_provider")
        async def f():
            pass

        await f()
        span = test_spans.pop()[0]
        assert_llmobs_span_data(
            _get_llmobs_data_metastruct(span),
            span_kind=decorator_name,
            model_name="test_model",
            model_provider="test_provider",
            tags={"decorator": "1"},
        )


def test_automatic_annotation_non_llm_decorators(llmobs, test_spans):
    """Test that automatic input/output annotation works for non-LLM decorators."""
    for decorator_name, decorator in (("task", task), ("workflow", workflow), ("tool", tool), ("agent", agent)):

        @decorator(name="test_function", session_id="test_session_id")
        def f(prompt, arg_2, kwarg_1=None, kwarg_2=None):
            return prompt

        f("test_prompt", "arg_2", kwarg_2=12345)
        span = test_spans.pop()[0]
        assert_llmobs_span_data(
            _get_llmobs_data_metastruct(span),
            span_kind=decorator_name,
            input_value='{"arg_2": "arg_2", "kwarg_2": 12345, "prompt": "test_prompt"}',
            output_value="test_prompt",
            tags={"session_id": "test_session_id", "decorator": "1"},
        )


def test_automatic_annotation_retrieval_decorator(llmobs, test_spans):
    """Test that automatic input annotation works for retrieval decorators."""

    @retrieval(session_id="test_session_id")
    def test_retrieval(query, arg_2, kwarg_1=None, kwarg_2=None):
        return [{"name": "name", "id": "1234567890", "score": 0.9}]

    test_retrieval("test_query", "arg_2", kwarg_2=12345)
    span = test_spans.pop()[0]
    assert_llmobs_span_data(
        _get_llmobs_data_metastruct(span),
        span_kind="retrieval",
        input_value='{"arg_2": "arg_2", "kwarg_2": 12345, "query": "test_query"}',
        tags={"session_id": "test_session_id", "decorator": "1"},
    )


def test_automatic_annotation_off_non_llm_decorators(llmobs, test_spans):
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
        span = test_spans.pop()[0]
        assert_llmobs_span_data(
            _get_llmobs_data_metastruct(span),
            span_kind=decorator_name,
            tags={"session_id": "test_session_id", "decorator": "1"},
        )


def test_automatic_annotation_off_if_manually_annotated(llmobs, test_spans):
    """Test disabling automatic input/output annotation for non-LLM decorators."""
    for decorator_name, decorator in (("task", task), ("workflow", workflow), ("tool", tool), ("agent", agent)):

        @decorator(name="test_function", session_id="test_session_id")
        def f(prompt, arg_2, kwarg_1=None, kwarg_2=None):
            llmobs.annotate(input_data="my custom input", output_data="my custom output")
            return prompt

        f("test_prompt", "arg_2", kwarg_2=12345)
        span = test_spans.pop()[0]
        assert_llmobs_span_data(
            _get_llmobs_data_metastruct(span),
            span_kind=decorator_name,
            input_value="my custom input",
            output_value="my custom output",
            tags={"session_id": "test_session_id", "decorator": "1"},
        )


def test_generator_sync(llmobs, test_spans):
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

        span = test_spans.pop()[0]
        if decorator_name == "llm":
            assert_llmobs_span_data(
                _get_llmobs_data_metastruct(span),
                span_kind=decorator_name,
                input_messages=[{"content": "hello"}],
                output_messages=[{"content": "world"}],
                model_name=UNKNOWN_MODEL_NAME,
                model_provider=UNKNOWN_MODEL_PROVIDER,
                tags={"decorator": "1"},
            )
        elif decorator_name == "embedding":
            assert_llmobs_span_data(
                _get_llmobs_data_metastruct(span),
                span_kind=decorator_name,
                input_documents=[{"text": "hello"}],
                output_value="world",
                model_name=UNKNOWN_MODEL_NAME,
                model_provider=UNKNOWN_MODEL_PROVIDER,
                tags={"decorator": "1"},
            )
        elif decorator_name == "retrieval":
            assert_llmobs_span_data(
                _get_llmobs_data_metastruct(span),
                span_kind=decorator_name,
                input_value="hello",
                output_documents=[{"text": "world"}],
                tags={"decorator": "1"},
            )
        else:
            assert_llmobs_span_data(
                _get_llmobs_data_metastruct(span),
                span_kind=decorator_name,
                input_value="hello",
                output_value="world",
                tags={"decorator": "1"},
            )


async def test_generator_async(llmobs, test_spans):
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

        span = test_spans.pop()[0]
        if decorator_name == "llm":
            assert_llmobs_span_data(
                _get_llmobs_data_metastruct(span),
                span_kind=decorator_name,
                input_messages=[{"content": "hello"}],
                output_messages=[{"content": "world"}],
                model_name=UNKNOWN_MODEL_NAME,
                model_provider=UNKNOWN_MODEL_PROVIDER,
                tags={"decorator": "1"},
            )
        elif decorator_name == "embedding":
            assert_llmobs_span_data(
                _get_llmobs_data_metastruct(span),
                span_kind=decorator_name,
                input_documents=[{"text": "hello"}],
                output_value="world",
                model_name=UNKNOWN_MODEL_NAME,
                model_provider=UNKNOWN_MODEL_PROVIDER,
                tags={"decorator": "1"},
            )
        elif decorator_name == "retrieval":
            assert_llmobs_span_data(
                _get_llmobs_data_metastruct(span),
                span_kind=decorator_name,
                input_value="hello",
                output_documents=[{"text": "world"}],
                tags={"decorator": "1"},
            )
        else:
            assert_llmobs_span_data(
                _get_llmobs_data_metastruct(span),
                span_kind=decorator_name,
                input_value="hello",
                output_value="world",
                tags={"decorator": "1"},
            )


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


def test_generator_sync_finishes_span_on_error(llmobs, test_spans):
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

    span = test_spans.pop()[0]
    assert_llmobs_span_data(
        _get_llmobs_data_metastruct(span),
        span_kind="workflow",
        error={
            "type": span.get_tag("error.type"),
            "message": span.get_tag("error.message"),
            "stack": span.get_tag("error.stack"),
        },
        tags={"decorator": "1"},
    )


async def test_generator_async_finishes_span_on_error(llmobs, test_spans):
    @workflow()
    async def f():
        for i in range(3):
            if i == 1:
                raise ValueError("test_error")
            yield i

    with pytest.raises(ValueError):
        async for _ in f():
            pass

    span = test_spans.pop()[0]
    assert_llmobs_span_data(
        _get_llmobs_data_metastruct(span),
        span_kind="workflow",
        error={
            "type": span.get_tag("error.type"),
            "message": span.get_tag("error.message"),
            "stack": span.get_tag("error.stack"),
        },
        tags={"decorator": "1"},
    )


def test_generator_sync_send(llmobs, test_spans):
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

    span = test_spans.pop()[0]
    assert_llmobs_span_data(
        _get_llmobs_data_metastruct(span),
        span_kind="workflow",
        tags={"decorator": "1"},
    )


async def test_generator_async_send(llmobs, test_spans):
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

    span = test_spans.pop()[0]
    assert_llmobs_span_data(
        _get_llmobs_data_metastruct(span),
        span_kind="workflow",
        tags={"decorator": "1"},
    )


def test_generator_sync_throw(llmobs, test_spans):
    @workflow()
    def f():
        for i in range(3):
            yield i

    with pytest.raises(ValueError):
        gen = f()
        next(gen)
        gen.throw(ValueError("test_error"))

    span = test_spans.pop()[0]
    assert_llmobs_span_data(
        _get_llmobs_data_metastruct(span),
        span_kind="workflow",
        error={
            "type": span.get_tag("error.type"),
            "message": span.get_tag("error.message"),
            "stack": span.get_tag("error.stack"),
        },
        tags={"decorator": "1"},
    )


async def test_generator_async_throw(llmobs, test_spans):
    @workflow()
    async def f():
        for i in range(3):
            yield i

    with pytest.raises(ValueError):
        gen = f()
        await gen.asend(None)
        await gen.athrow(ValueError("test_error"))

    span = test_spans.pop()[0]
    assert_llmobs_span_data(
        _get_llmobs_data_metastruct(span),
        span_kind="workflow",
        error={
            "type": span.get_tag("error.type"),
            "message": span.get_tag("error.message"),
            "stack": span.get_tag("error.stack"),
        },
        tags={"decorator": "1"},
    )


def test_generator_exit_exception_sync(llmobs, test_spans):
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

    span = test_spans.pop()[0]
    assert_llmobs_span_data(
        _get_llmobs_data_metastruct(span),
        span_kind="workflow",
        input_value='{"alist": [0, 1, 2, 3, 4, 5, 6, 7, 8, 9]}',
        error={
            "type": span.get_tag("error.type"),
            "message": span.get_tag("error.message"),
            "stack": span.get_tag("error.stack"),
        },
        tags={"decorator": "1"},
    )


@pytest.mark.parametrize(
    "decorator",
    [task, tool, workflow, agent],
    ids=["task", "tool", "workflow", "agent"],
)
def test_generator_for_class_does_not_annotate_self(llmobs, test_spans, decorator):
    class TestClass:
        @decorator
        def add(self, a: int, b: int) -> int:
            return a + b

    test_class = TestClass()
    test_class.add(1, 2)

    spans = [s for trace in test_spans.pop_traces() for s in trace if get_llmobs_span_kind(s)]
    assert len(spans) == 1

    input_value = json.loads(get_llmobs_input_value(spans[0]))
    assert input_value == {"a": 1, "b": 2}
