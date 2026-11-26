from collections import OrderedDict
from functools import wraps
from inspect import isasyncgenfunction
from inspect import iscoroutinefunction
from inspect import isgeneratorfunction
from inspect import signature
import sys
from typing import Any
from typing import AsyncGenerator
from typing import Callable
from typing import Generator
from typing import Optional
from typing import Protocol
from typing import TypeVar
from typing import Union
from typing import overload

from ddtrace.internal.logger import get_logger
from ddtrace.llmobs import LLMObs
from ddtrace.llmobs._constants import SPAN_START_WHILE_DISABLED_WARNING
from ddtrace.llmobs._constants import UNKNOWN_MODEL_NAME
from ddtrace.llmobs._llmobs import LLMObsAnnotateSpanError
from ddtrace.llmobs._span import _LLMObsSpan
from ddtrace.llmobs._utils import get_llmobs_output_messages
from ddtrace.llmobs._utils import get_llmobs_output_value
from ddtrace.llmobs._utils import get_llmobs_span_kind


log = get_logger(__name__)

F = TypeVar("F", bound=Callable[..., Any])


class _ModelDecorator(Protocol):
    """Callable usable either as ``@llm`` or ``@llm(model_name=...)``."""

    @overload
    def __call__(
        self,
        original_func: F,
        model_name: Optional[str] = None,
        model_provider: Optional[str] = None,
        name: Optional[str] = None,
        session_id: Optional[str] = None,
        ml_app: Optional[str] = None,
        agent_service: Optional[str] = None,
    ) -> F: ...

    @overload
    def __call__(
        self,
        original_func: None = None,
        model_name: Optional[str] = None,
        model_provider: Optional[str] = None,
        name: Optional[str] = None,
        session_id: Optional[str] = None,
        ml_app: Optional[str] = None,
        agent_service: Optional[str] = None,
    ) -> Callable[[F], F]: ...


class _LLMObsDecorator(Protocol):
    """Callable usable either as ``@workflow`` or ``@workflow(name=...)``."""

    @overload
    def __call__(
        self,
        original_func: F,
        name: Optional[str] = None,
        session_id: Optional[str] = None,
        ml_app: Optional[str] = None,
        agent_service: Optional[str] = None,
        version: Optional[str] = None,
        _automatic_io_annotation: bool = True,
    ) -> F: ...

    @overload
    def __call__(
        self,
        original_func: None = None,
        name: Optional[str] = None,
        session_id: Optional[str] = None,
        ml_app: Optional[str] = None,
        agent_service: Optional[str] = None,
        version: Optional[str] = None,
        _automatic_io_annotation: bool = True,
    ) -> Callable[[F], F]: ...


def _get_llmobs_span_options(
    name: Optional[str], model_name: Optional[str], func: Callable[..., Any]
) -> tuple[str, str]:
    traced_model_name = model_name
    if traced_model_name is None:
        traced_model_name = UNKNOWN_MODEL_NAME

    span_name = name
    if span_name is None:
        span_name = func.__name__

    return traced_model_name, span_name


def _get_span_inputs(args: "OrderedDict[str, Any]") -> dict[str, Any]:
    return {arg: value for arg, value in args.items() if arg != "self"}


async def yield_from_async_gen(
    func: Callable[..., AsyncGenerator[Any, Any]], span: _LLMObsSpan, args: tuple[Any, ...], kwargs: dict[str, Any]
) -> AsyncGenerator[Any, Any]:
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


def _model_decorator(operation_kind: str) -> _ModelDecorator:
    def decorator(
        original_func: Optional[Callable[..., Any]] = None,
        model_name: Optional[str] = None,
        model_provider: Optional[str] = None,
        name: Optional[str] = None,
        session_id: Optional[str] = None,
        ml_app: Optional[str] = None,
        agent_service: Optional[str] = None,
    ) -> Any:
        def inner(func: Callable[..., Any]) -> Callable[..., Any]:
            if iscoroutinefunction(func) or isasyncgenfunction(func):

                @wraps(func)
                def generator_wrapper(*args: Any, **kwargs: Any) -> Any:
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
                        agent_service=agent_service,
                        _decorator=True,
                    )
                    return yield_from_async_gen(func, span, args, kwargs)

                @wraps(func)
                async def wrapper(*args: Any, **kwargs: Any) -> Any:
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
                        agent_service=agent_service,
                        _decorator=True,
                    ) as span:
                        resp = await func(*args, **kwargs)
                        if (
                            resp is not None
                            and get_llmobs_output_value(span) is None
                            and get_llmobs_output_messages(span) is None
                            and operation_kind != "embedding"
                        ):
                            try:
                                LLMObs.annotate(span=span, output_data=resp)
                            except LLMObsAnnotateSpanError:
                                log.debug(
                                    "Failed to auto-annotate output for @%s decorated function. "
                                    "Use LLMObs.annotate() to manually annotate the output.",
                                    operation_kind,
                                )
                        return resp

            else:

                @wraps(func)
                def generator_wrapper(*args: Any, **kwargs: Any) -> Generator[Any, Any, None]:
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
                            agent_service=agent_service,
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
                def wrapper(*args: Any, **kwargs: Any) -> Any:
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
                        agent_service=agent_service,
                        _decorator=True,
                    ) as span:
                        resp = func(*args, **kwargs)
                        if (
                            resp is not None
                            and get_llmobs_output_value(span) is None
                            and get_llmobs_output_messages(span) is None
                            and operation_kind != "embedding"
                        ):
                            try:
                                LLMObs.annotate(span=span, output_data=resp)
                            except LLMObsAnnotateSpanError:
                                log.debug(
                                    "Failed to auto-annotate output for @%s decorated function. "
                                    "Use LLMObs.annotate() to manually annotate the output.",
                                    operation_kind,
                                )
                        return resp

            return generator_wrapper if (isgeneratorfunction(func) or isasyncgenfunction(func)) else wrapper

        if original_func and callable(original_func):
            return inner(original_func)
        return inner

    return decorator


def _wrap_llmobs_func(
    func: Callable[..., Any],
    start_span: Callable[[Callable[..., Any]], Any],
    automatic_io_annotation: bool,
) -> Callable[..., Any]:
    def _annotate_inputs(span: Any, args: tuple[Any, ...], kwargs: dict[str, Any]) -> None:
        if not automatic_io_annotation:
            return
        bound_args = signature(func).bind_partial(*args, **kwargs)
        if bound_args.arguments:
            LLMObs.annotate(span=span, input_data=_get_span_inputs(bound_args.arguments))

    def _annotate_output(span: Any, resp: Any) -> None:
        if automatic_io_annotation and resp is not None and get_llmobs_span_kind(span) != "retrieval":
            if get_llmobs_output_value(span) is None:
                LLMObs.annotate(span=span, output_data=resp)

    if iscoroutinefunction(func) or isasyncgenfunction(func):

        @wraps(func)
        def generator_wrapper(*args: Any, **kwargs: Any) -> Any:
            if not LLMObs.enabled:
                log.warning(SPAN_START_WHILE_DISABLED_WARNING)
                return func(*args, **kwargs)
            span = start_span(func)
            try:
                _annotate_inputs(span, args, kwargs)
            except Exception:
                span.set_exc_info(*sys.exc_info())
                span.finish()
                raise
            except BaseException:
                span.finish()
                raise
            return yield_from_async_gen(func, span, args, kwargs)

        @wraps(func)
        async def wrapper(*args: Any, **kwargs: Any) -> Any:
            if not LLMObs.enabled:
                log.warning(SPAN_START_WHILE_DISABLED_WARNING)
                return await func(*args, **kwargs)
            with start_span(func) as span:
                _annotate_inputs(span, args, kwargs)
                resp = await func(*args, **kwargs)
                _annotate_output(span, resp)
                return resp

    else:

        @wraps(func)
        def generator_wrapper(*args: Any, **kwargs: Any) -> Generator[Any, Any, None]:
            if not LLMObs.enabled:
                log.warning(SPAN_START_WHILE_DISABLED_WARNING)
                yield from func(*args, **kwargs)
            else:
                span = start_span(func)
                try:
                    _annotate_inputs(span, args, kwargs)
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
        def wrapper(*args: Any, **kwargs: Any) -> Any:
            if not LLMObs.enabled:
                log.warning(SPAN_START_WHILE_DISABLED_WARNING)
                return func(*args, **kwargs)
            with start_span(func) as span:
                _annotate_inputs(span, args, kwargs)
                resp = func(*args, **kwargs)
                _annotate_output(span, resp)
                return resp

    return generator_wrapper if (isgeneratorfunction(func) or isasyncgenfunction(func)) else wrapper


def _llmobs_decorator(operation_kind: str) -> _LLMObsDecorator:
    def decorator(
        original_func: Optional[Callable[..., Any]] = None,
        name: Optional[str] = None,
        session_id: Optional[str] = None,
        ml_app: Optional[str] = None,
        agent_service: Optional[str] = None,
        version: Optional[str] = None,
        _automatic_io_annotation: bool = True,
    ) -> Any:
        if version is not None and operation_kind != "agent":
            log.warning(
                "The `version` argument is only supported on @agent, ignoring it for @%s.",
                operation_kind,
            )
        version_kwargs = {"version": version} if operation_kind == "agent" and version is not None else {}

        def _start_span(func: Callable[..., Any]) -> Any:
            _, span_name = _get_llmobs_span_options(name, None, func)
            traced_operation = getattr(LLMObs, operation_kind, LLMObs.workflow)
            return traced_operation(
                name=span_name,
                session_id=session_id,
                ml_app=ml_app,
                agent_service=agent_service,
                _decorator=True,
                **version_kwargs,
            )

        def inner(func: Callable[..., Any]) -> Callable[..., Any]:
            return _wrap_llmobs_func(func, _start_span, _automatic_io_annotation)

        if original_func and callable(original_func):
            return inner(original_func)
        return inner

    return decorator


@overload
def memory(
    operation: F,
    name: Optional[str] = None,
    store_id: Optional[str] = None,
    memory_type: Optional[str] = None,
    scope: Optional[str] = None,
    provider: str = "custom",
    principal: Optional[str] = None,
    tenant_id: Optional[str] = None,
    namespace: Optional[str] = None,
    provenance_source: Optional[str] = None,
    provenance_span_id: Optional[str] = None,
    session_id: Optional[str] = None,
    ml_app: Optional[str] = None,
    agent_service: Optional[str] = None,
    _automatic_io_annotation: bool = True,
) -> F: ...


@overload
def memory(
    operation: str = "search_memory",
    name: Optional[str] = None,
    store_id: Optional[str] = None,
    memory_type: Optional[str] = None,
    scope: Optional[str] = None,
    provider: str = "custom",
    principal: Optional[str] = None,
    tenant_id: Optional[str] = None,
    namespace: Optional[str] = None,
    provenance_source: Optional[str] = None,
    provenance_span_id: Optional[str] = None,
    session_id: Optional[str] = None,
    ml_app: Optional[str] = None,
    agent_service: Optional[str] = None,
    _automatic_io_annotation: bool = True,
) -> Callable[[F], F]: ...


def memory(
    operation: Union[str, Callable[..., Any]] = "search_memory",
    name: Optional[str] = None,
    store_id: Optional[str] = None,
    memory_type: Optional[str] = None,
    scope: Optional[str] = None,
    provider: str = "custom",
    principal: Optional[str] = None,
    tenant_id: Optional[str] = None,
    namespace: Optional[str] = None,
    provenance_source: Optional[str] = None,
    provenance_span_id: Optional[str] = None,
    session_id: Optional[str] = None,
    ml_app: Optional[str] = None,
    agent_service: Optional[str] = None,
    _automatic_io_annotation: bool = True,
) -> Any:
    original_func: Optional[Callable[..., Any]] = None
    if callable(operation):
        original_func = operation
        resolved_op = "search_memory"
    else:
        resolved_op = operation

    def _start_memory_span(func: Callable[..., Any]) -> Any:
        _, span_name = _get_llmobs_span_options(name, None, func)
        return LLMObs.memory(
            operation=resolved_op,
            name=span_name,
            store_id=store_id,
            memory_type=memory_type,
            scope=scope,
            provider=provider,
            principal=principal,
            tenant_id=tenant_id,
            namespace=namespace,
            provenance_source=provenance_source,
            provenance_span_id=provenance_span_id,
            session_id=session_id,
            ml_app=ml_app,
            agent_service=agent_service,
            _decorator=True,
        )

    def inner(func: Callable[..., Any]) -> Callable[..., Any]:
        return _wrap_llmobs_func(func, _start_memory_span, _automatic_io_annotation)

    if original_func is not None:
        return inner(original_func)
    return inner


llm = _model_decorator("llm")
embedding = _model_decorator("embedding")
workflow = _llmobs_decorator("workflow")
task = _llmobs_decorator("task")
tool = _llmobs_decorator("tool")
retrieval = _llmobs_decorator("retrieval")
agent = _llmobs_decorator("agent")
