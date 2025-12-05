from functools import wraps
from inspect import isasyncgenfunction
from inspect import iscoroutinefunction
from inspect import isgeneratorfunction
from inspect import signature
import sys
from typing import Callable
from typing import Optional
from typing import OrderedDict
from typing import TypeVar
from typing import overload

from ddtrace.internal.logger import get_logger
from ddtrace.llmobs import LLMObs
from ddtrace.llmobs._constants import OUTPUT_VALUE
from ddtrace.llmobs._constants import SPAN_START_WHILE_DISABLED_WARNING


log = get_logger(__name__)

# Type variable for preserving function signatures
F = TypeVar("F", bound=Callable)


def _get_llmobs_span_options(name, model_name, func):
    traced_model_name = model_name
    if traced_model_name is None:
        traced_model_name = "custom"

    span_name = name
    if span_name is None:
        span_name = func.__name__

    return traced_model_name, span_name


def _get_span_inputs(args: OrderedDict) -> dict:
    return {arg: value for arg, value in args.items() if arg != "self"}


async def yield_from_async_gen(func, span, args, kwargs):
    try:
        gen = func(*args, **kwargs)
        next_val = await gen.asend(None)
        while True:
            try:
                i = yield next_val
                next_val = await gen.asend(i)
            except GeneratorExit:
                await gen.aclose()
                break
            except StopAsyncIteration as e:
                await gen.athrow(e)
                break
            except Exception as e:
                await gen.athrow(e)
                raise
    except (StopAsyncIteration, GeneratorExit):
        raise
    except Exception:
        span.set_exc_info(*sys.exc_info())
        raise
    finally:
        span.finish()


def _model_decorator(operation_kind):
    def decorator(
        original_func: Optional[Callable] = None,
        model_name: Optional[str] = None,
        model_provider: Optional[str] = None,
        name: Optional[str] = None,
        session_id: Optional[str] = None,
        ml_app: Optional[str] = None,
    ):
        def inner(func):
            if iscoroutinefunction(func) or isasyncgenfunction(func):

                @wraps(func)
                def generator_wrapper(*args, **kwargs):
                    if not LLMObs.enabled:
                        log.warning(SPAN_START_WHILE_DISABLED_WARNING)
                        return func(*args, **kwargs)
                    traced_model_name, span_name = _get_llmobs_span_options(name, model_name, func)
                    traced_operation = getattr(LLMObs, operation_kind, LLMObs.llm)
                    span = traced_operation(
                        model_name=traced_model_name,
                        model_provider=model_provider,
                        name=span_name,
                        session_id=session_id,
                        ml_app=ml_app,
                        _decorator=True,
                    )
                    return yield_from_async_gen(func, span, args, kwargs)

                @wraps(func)
                async def wrapper(*args, **kwargs):
                    if not LLMObs.enabled:
                        log.warning(SPAN_START_WHILE_DISABLED_WARNING)
                        return await func(*args, **kwargs)
                    traced_model_name, span_name = _get_llmobs_span_options(name, model_name, func)
                    traced_operation = getattr(LLMObs, operation_kind, LLMObs.llm)
                    with traced_operation(
                        model_name=traced_model_name,
                        model_provider=model_provider,
                        name=span_name,
                        session_id=session_id,
                        ml_app=ml_app,
                        _decorator=True,
                    ):
                        return await func(*args, **kwargs)

            else:

                @wraps(func)
                def generator_wrapper(*args, **kwargs):
                    if not LLMObs.enabled:
                        log.warning(SPAN_START_WHILE_DISABLED_WARNING)
                        yield from func(*args, **kwargs)
                    else:
                        traced_model_name, span_name = _get_llmobs_span_options(name, model_name, func)
                        traced_operation = getattr(LLMObs, operation_kind, LLMObs.llm)
                        span = traced_operation(
                            model_name=traced_model_name,
                            model_provider=model_provider,
                            name=span_name,
                            session_id=session_id,
                            ml_app=ml_app,
                            _decorator=True,
                        )
                        try:
                            yield from func(*args, **kwargs)
                        except (StopIteration, GeneratorExit):
                            raise
                        except Exception:
                            span.set_exc_info(*sys.exc_info())
                            raise
                        finally:
                            span.finish()

                @wraps(func)
                def wrapper(*args, **kwargs):
                    if not LLMObs.enabled:
                        log.warning(SPAN_START_WHILE_DISABLED_WARNING)
                        return func(*args, **kwargs)
                    traced_model_name, span_name = _get_llmobs_span_options(name, model_name, func)
                    traced_operation = getattr(LLMObs, operation_kind, LLMObs.llm)
                    with traced_operation(
                        model_name=traced_model_name,
                        model_provider=model_provider,
                        name=span_name,
                        session_id=session_id,
                        ml_app=ml_app,
                        _decorator=True,
                    ):
                        return func(*args, **kwargs)

            return generator_wrapper if (isgeneratorfunction(func) or isasyncgenfunction(func)) else wrapper

        if original_func and callable(original_func):
            return inner(original_func)
        return inner

    return decorator


def _llmobs_decorator(operation_kind):
    def decorator(
        original_func: Optional[Callable] = None,
        name: Optional[str] = None,
        session_id: Optional[str] = None,
        ml_app: Optional[str] = None,
        _automatic_io_annotation: bool = True,
    ):
        def inner(func):
            if iscoroutinefunction(func) or isasyncgenfunction(func):

                @wraps(func)
                def generator_wrapper(*args, **kwargs):
                    if not LLMObs.enabled:
                        log.warning(SPAN_START_WHILE_DISABLED_WARNING)
                        return func(*args, **kwargs)
                    _, span_name = _get_llmobs_span_options(name, None, func)
                    traced_operation = getattr(LLMObs, operation_kind, LLMObs.workflow)
                    span = traced_operation(name=span_name, session_id=session_id, ml_app=ml_app, _decorator=True)
                    func_signature = signature(func)
                    bound_args = func_signature.bind_partial(*args, **kwargs)
                    if _automatic_io_annotation and bound_args.arguments:
                        LLMObs.annotate(span=span, input_data=_get_span_inputs(bound_args.arguments))
                    return yield_from_async_gen(func, span, args, kwargs)

                @wraps(func)
                async def wrapper(*args, **kwargs):
                    if not LLMObs.enabled:
                        log.warning(SPAN_START_WHILE_DISABLED_WARNING)
                        return await func(*args, **kwargs)
                    _, span_name = _get_llmobs_span_options(name, None, func)
                    traced_operation = getattr(LLMObs, operation_kind, LLMObs.workflow)
                    with traced_operation(
                        name=span_name, session_id=session_id, ml_app=ml_app, _decorator=True
                    ) as span:
                        func_signature = signature(func)
                        bound_args = func_signature.bind_partial(*args, **kwargs)
                        if _automatic_io_annotation and bound_args.arguments:
                            LLMObs.annotate(span=span, input_data=_get_span_inputs(bound_args.arguments))
                        resp = await func(*args, **kwargs)
                        if (
                            _automatic_io_annotation
                            and resp is not None
                            and operation_kind != "retrieval"
                            and span._get_ctx_item(OUTPUT_VALUE) is None
                        ):
                            LLMObs.annotate(span=span, output_data=resp)
                        return resp

            else:

                @wraps(func)
                def generator_wrapper(*args, **kwargs):
                    if not LLMObs.enabled:
                        log.warning(SPAN_START_WHILE_DISABLED_WARNING)
                        yield from func(*args, **kwargs)
                    else:
                        _, span_name = _get_llmobs_span_options(name, None, func)
                        traced_operation = getattr(LLMObs, operation_kind, LLMObs.workflow)
                        span = traced_operation(name=span_name, session_id=session_id, ml_app=ml_app, _decorator=True)
                        func_signature = signature(func)
                        bound_args = func_signature.bind_partial(*args, **kwargs)
                        if _automatic_io_annotation and bound_args.arguments:
                            LLMObs.annotate(span=span, input_data=_get_span_inputs(bound_args.arguments))
                        try:
                            yield from func(*args, **kwargs)
                        except (StopIteration, GeneratorExit):
                            raise
                        except Exception:
                            span.set_exc_info(*sys.exc_info())
                            raise
                        finally:
                            if span:
                                span.finish()

                @wraps(func)
                def wrapper(*args, **kwargs):
                    if not LLMObs.enabled:
                        log.warning(SPAN_START_WHILE_DISABLED_WARNING)
                        return func(*args, **kwargs)
                    _, span_name = _get_llmobs_span_options(name, None, func)
                    traced_operation = getattr(LLMObs, operation_kind, LLMObs.workflow)
                    with traced_operation(
                        name=span_name, session_id=session_id, ml_app=ml_app, _decorator=True
                    ) as span:
                        func_signature = signature(func)
                        bound_args = func_signature.bind_partial(*args, **kwargs)
                        if _automatic_io_annotation and bound_args.arguments:
                            LLMObs.annotate(span=span, input_data=_get_span_inputs(bound_args.arguments))
                        resp = func(*args, **kwargs)
                        if (
                            _automatic_io_annotation
                            and resp is not None
                            and operation_kind != "retrieval"
                            and span._get_ctx_item(OUTPUT_VALUE) is None
                        ):
                            LLMObs.annotate(span=span, output_data=resp)
                        return resp

            return generator_wrapper if (isgeneratorfunction(func) or isasyncgenfunction(func)) else wrapper

        if original_func and callable(original_func):
            return inner(original_func)
        return inner

    return decorator


# Type-hinted decorator exports with overloads for better IDE support

@overload
def llm(func: F, /) -> F:
    """Trace an LLM invocation without parameters."""
    ...


@overload
def llm(
    *,
    model_name: Optional[str] = None,
    model_provider: Optional[str] = None,
    name: Optional[str] = None,
    session_id: Optional[str] = None,
    ml_app: Optional[str] = None,
) -> Callable[[F], F]:
    """Trace an LLM invocation.

    Args:
        model_name: The name of the invoked LLM (e.g., "gpt-4", "claude-3"). Defaults to "custom".
        model_provider: The provider/company of the LLM (e.g., "openai", "anthropic"). Defaults to "custom".
        name: The name of the traced operation. Defaults to the function name.
        session_id: The ID of the session for grouping traces.
        ml_app: The name of the ML application.

    Returns:
        A decorator that traces the function as an LLM span.

    Example:
        @llm(model_name="gpt-4", model_provider="openai")
        def generate_text(prompt: str) -> str:
            return client.chat.completions.create(...)
    """
    ...


def llm(
    func: Optional[F] = None,
    /,
    *,
    model_name: Optional[str] = None,
    model_provider: Optional[str] = None,
    name: Optional[str] = None,
    session_id: Optional[str] = None,
    ml_app: Optional[str] = None,
) -> Callable[[F], F]:
    """Trace an LLM invocation."""
    return _model_decorator("llm")(func, model_name=model_name, model_provider=model_provider, name=name, session_id=session_id, ml_app=ml_app)  # type: ignore[return-value]


@overload
def embedding(func: F, /) -> F:
    """Trace an embedding operation without parameters."""
    ...


@overload
def embedding(
    *,
    model_name: Optional[str] = None,
    model_provider: Optional[str] = None,
    name: Optional[str] = None,
    session_id: Optional[str] = None,
    ml_app: Optional[str] = None,
) -> Callable[[F], F]:
    """Trace an embedding model invocation.

    Args:
        model_name: The name of the embedding model (e.g., "text-embedding-ada-002"). Defaults to "custom".
        model_provider: The provider of the embedding model (e.g., "openai", "cohere"). Defaults to "custom".
        name: The name of the traced operation. Defaults to the function name.
        session_id: The ID of the session for grouping traces.
        ml_app: The name of the ML application.

    Returns:
        A decorator that traces the function as an embedding span.

    Example:
        @embedding(model_name="text-embedding-ada-002", model_provider="openai")
        def embed_text(text: str) -> list[float]:
            return client.embeddings.create(...)
    """
    ...


def embedding(
    func: Optional[F] = None,
    /,
    *,
    model_name: Optional[str] = None,
    model_provider: Optional[str] = None,
    name: Optional[str] = None,
    session_id: Optional[str] = None,
    ml_app: Optional[str] = None,
) -> Callable[[F], F]:
    """Trace an embedding model invocation."""
    return _model_decorator("embedding")(func, model_name=model_name, model_provider=model_provider, name=name, session_id=session_id, ml_app=ml_app)  # type: ignore[return-value]


@overload
def workflow(func: F, /) -> F:
    """Trace a workflow without parameters."""
    ...


@overload
def workflow(
    *,
    name: Optional[str] = None,
    session_id: Optional[str] = None,
    ml_app: Optional[str] = None,
) -> Callable[[F], F]:
    """Trace a workflow - a predefined sequence of operations.

    Workflows represent a series of operations with a well-defined flow, such as a
    RAG pipeline or multi-step LLM chain. Automatically captures input/output.

    Args:
        name: The name of the workflow. Defaults to the function name.
        session_id: The ID of the session for grouping traces.
        ml_app: The name of the ML application.

    Returns:
        A decorator that traces the function as a workflow span.

    Example:
        @workflow(name="rag_pipeline")
        def process_query(query: str) -> str:
            docs = retrieve(query)
            return generate_answer(docs, query)
    """
    ...


def workflow(
    func: Optional[F] = None,
    /,
    *,
    name: Optional[str] = None,
    session_id: Optional[str] = None,
    ml_app: Optional[str] = None,
) -> Callable[[F], F]:
    """Trace a workflow."""
    return _llmobs_decorator("workflow")(func, name=name, session_id=session_id, ml_app=ml_app)  # type: ignore[return-value]


@overload
def task(func: F, /) -> F:
    """Trace a task without parameters."""
    ...


@overload
def task(
    *,
    name: Optional[str] = None,
    session_id: Optional[str] = None,
    ml_app: Optional[str] = None,
) -> Callable[[F], F]:
    """Trace a task - a standalone non-LLM operation.

    Tasks represent discrete units of work that don't involve LLM calls, such as
    data processing, validation, or formatting. Automatically captures input/output.

    Args:
        name: The name of the task. Defaults to the function name.
        session_id: The ID of the session for grouping traces.
        ml_app: The name of the ML application.

    Returns:
        A decorator that traces the function as a task span.

    Example:
        @task(name="parse_documents")
        def parse_docs(raw_data: str) -> list[dict]:
            return json.loads(raw_data)
    """
    ...


def task(
    func: Optional[F] = None,
    /,
    *,
    name: Optional[str] = None,
    session_id: Optional[str] = None,
    ml_app: Optional[str] = None,
) -> Callable[[F], F]:
    """Trace a task."""
    return _llmobs_decorator("task")(func, name=name, session_id=session_id, ml_app=ml_app)  # type: ignore[return-value]


@overload
def tool(func: F, /) -> F:
    """Trace a tool without parameters."""
    ...


@overload
def tool(
    *,
    name: Optional[str] = None,
    session_id: Optional[str] = None,
    ml_app: Optional[str] = None,
) -> Callable[[F], F]:
    """Trace a tool - an external API or interface call.

    Tools represent calls to external services, APIs, or tools that an agent or
    LLM application uses to perform actions. Automatically captures input/output.

    Args:
        name: The name of the tool. Defaults to the function name.
        session_id: The ID of the session for grouping traces.
        ml_app: The name of the ML application.

    Returns:
        A decorator that traces the function as a tool span.

    Example:
        @tool(name="weather_api")
        def get_weather(location: str) -> dict:
            return requests.get(f"https://api.weather.com/{location}").json()
    """
    ...


def tool(
    func: Optional[F] = None,
    /,
    *,
    name: Optional[str] = None,
    session_id: Optional[str] = None,
    ml_app: Optional[str] = None,
) -> Callable[[F], F]:
    """Trace a tool."""
    return _llmobs_decorator("tool")(func, name=name, session_id=session_id, ml_app=ml_app)  # type: ignore[return-value]


@overload
def retrieval(func: F, /) -> F:
    """Trace a retrieval without parameters."""
    ...


@overload
def retrieval(
    *,
    name: Optional[str] = None,
    session_id: Optional[str] = None,
    ml_app: Optional[str] = None,
) -> Callable[[F], F]:
    """Trace a retrieval operation - vector search or document retrieval.

    Retrieval operations represent searches through document stores, vector databases,
    or knowledge bases. Automatically captures input and expects documents as output.

    Args:
        name: The name of the retrieval operation. Defaults to the function name.
        session_id: The ID of the session for grouping traces.
        ml_app: The name of the ML application.

    Returns:
        A decorator that traces the function as a retrieval span.

    Example:
        @retrieval(name="vector_search")
        def search_docs(query: str) -> list[dict]:
            return vector_db.similarity_search(query, k=5)
    """
    ...


def retrieval(
    func: Optional[F] = None,
    /,
    *,
    name: Optional[str] = None,
    session_id: Optional[str] = None,
    ml_app: Optional[str] = None,
) -> Callable[[F], F]:
    """Trace a retrieval operation."""
    return _llmobs_decorator("retrieval")(func, name=name, session_id=session_id, ml_app=ml_app)  # type: ignore[return-value]


@overload
def agent(func: F, /) -> F:
    """Trace an agent without parameters."""
    ...


@overload
def agent(
    *,
    name: Optional[str] = None,
    session_id: Optional[str] = None,
    ml_app: Optional[str] = None,
) -> Callable[[F], F]:
    """Trace an agent - a dynamic, autonomous workflow.

    Agents represent systems that make dynamic decisions about which actions to take,
    such as ReAct agents or autonomous planning systems. Automatically captures input/output.

    Args:
        name: The name of the agent. Defaults to the function name.
        session_id: The ID of the session for grouping traces.
        ml_app: The name of the ML application.

    Returns:
        A decorator that traces the function as an agent span.

    Example:
        @agent(name="research_agent")
        def autonomous_research(topic: str) -> str:
            # Agent decides which tools to use and when
            return perform_research(topic)
    """
    ...


def agent(
    func: Optional[F] = None,
    /,
    *,
    name: Optional[str] = None,
    session_id: Optional[str] = None,
    ml_app: Optional[str] = None,
) -> Callable[[F], F]:
    """Trace an agent."""
    return _llmobs_decorator("agent")(func, name=name, session_id=session_id, ml_app=ml_app)  # type: ignore[return-value]


@overload
def trace(func: F, /) -> F:
    """Trace a generic operation without parameters."""
    ...


@overload
def trace(
    *,
    name: Optional[str] = None,
    session_id: Optional[str] = None,
    ml_app: Optional[str] = None,
) -> Callable[[F], F]:
    """Trace a generic LLM operation (defaults to workflow span).

    Generic decorator for tracing any LLM-related operation. Creates a workflow span
    by default. Use more specific decorators (@llm, @task, @tool, etc.) when the
    operation type is known. Automatically captures input/output.

    Args:
        name: The name of the operation. Defaults to the function name.
        session_id: The ID of the session for grouping traces.
        ml_app: The name of the ML application.

    Returns:
        A decorator that traces the function as a workflow span.

    Example:
        @trace
        def process_request(user_input: str) -> str:
            return handle_llm_workflow(user_input)
    """
    ...


def trace(
    func: Optional[F] = None,
    /,
    *,
    name: Optional[str] = None,
    session_id: Optional[str] = None,
    ml_app: Optional[str] = None,
) -> Callable[[F], F]:
    """Trace a generic LLM operation."""
    return _llmobs_decorator("workflow")(func, name=name, session_id=session_id, ml_app=ml_app)  # type: ignore[return-value]
