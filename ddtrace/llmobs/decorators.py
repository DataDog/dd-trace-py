from functools import wraps
from inspect import signature
from typing import Callable
from typing import Optional

from ddtrace.internal.compat import iscoroutinefunction
from ddtrace.internal.logger import get_logger
from ddtrace.llmobs import LLMObs
from ddtrace.llmobs._constants import OUTPUT_VALUE
from ddtrace.llmobs._constants import SPAN_START_WHILE_DISABLED_WARNING


log = get_logger(__name__)


def _model_decorator(operation_kind):
    def decorator(
        model_name: str,
        model_provider: Optional[str] = None,
        name: Optional[str] = None,
        session_id: Optional[str] = None,
        ml_app: Optional[str] = None,
    ):
        def inner(func):
            if iscoroutinefunction(func):

                @wraps(func)
                async def wrapper(*args, **kwargs):
                    if not LLMObs.enabled:
                        log.warning(SPAN_START_WHILE_DISABLED_WARNING)
                        return await func(*args, **kwargs)
                    traced_model_name = model_name
                    if traced_model_name is None:
                        log.warning("model_name missing for LLMObs.%s() - default to 'unknown'", operation_kind)
                        traced_model_name = "unknown"
                    span_name = name
                    if span_name is None:
                        span_name = func.__name__
                    traced_operation = getattr(LLMObs, operation_kind, "llm")
                    with traced_operation(
                        model_name=traced_model_name,
                        model_provider=model_provider,
                        name=span_name,
                        session_id=session_id,
                        ml_app=ml_app,
                    ):
                        return await func(*args, **kwargs)

            else:

                @wraps(func)
                def wrapper(*args, **kwargs):
                    if not LLMObs.enabled:
                        log.warning(SPAN_START_WHILE_DISABLED_WARNING)
                        return func(*args, **kwargs)
                    traced_model_name = model_name
                    if traced_model_name is None:
                        log.warning("model_name missing for LLMObs.%s() - default to 'unknown'", operation_kind)
                        traced_model_name = "unknown"
                    span_name = name
                    if span_name is None:
                        span_name = func.__name__
                    traced_operation = getattr(LLMObs, operation_kind, "llm")
                    with traced_operation(
                        model_name=traced_model_name,
                        model_provider=model_provider,
                        name=span_name,
                        session_id=session_id,
                        ml_app=ml_app,
                    ):
                        return func(*args, **kwargs)

            return wrapper

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
            if iscoroutinefunction(func):

                @wraps(func)
                async def wrapper(*args, **kwargs):
                    if not LLMObs.enabled:
                        log.warning(SPAN_START_WHILE_DISABLED_WARNING)
                        return await func(*args, **kwargs)
                    span_name = name
                    if span_name is None:
                        span_name = func.__name__
                    traced_operation = getattr(LLMObs, operation_kind, "workflow")
                    with traced_operation(name=span_name, session_id=session_id, ml_app=ml_app) as span:
                        func_signature = signature(func)
                        bound_args = func_signature.bind_partial(*args, **kwargs)
                        if _automatic_io_annotation and bound_args.arguments:
                            LLMObs.annotate(span=span, input_data=bound_args.arguments)
                        resp = await func(*args, **kwargs)
                        if (
                            _automatic_io_annotation
                            and resp
                            and operation_kind != "retrieval"
                            and span.get_tag(OUTPUT_VALUE) is None
                        ):
                            LLMObs.annotate(span=span, output_data=resp)
                        return resp

            else:

                @wraps(func)
                def wrapper(*args, **kwargs):
                    if not LLMObs.enabled:
                        log.warning(SPAN_START_WHILE_DISABLED_WARNING)
                        return func(*args, **kwargs)
                    span_name = name
                    if span_name is None:
                        span_name = func.__name__
                    traced_operation = getattr(LLMObs, operation_kind, "workflow")
                    with traced_operation(name=span_name, session_id=session_id, ml_app=ml_app) as span:
                        func_signature = signature(func)
                        bound_args = func_signature.bind_partial(*args, **kwargs)
                        if _automatic_io_annotation and bound_args.arguments:
                            LLMObs.annotate(span=span, input_data=bound_args.arguments)
                        resp = func(*args, **kwargs)
                        if (
                            _automatic_io_annotation
                            and resp
                            and operation_kind != "retrieval"
                            and span.get_tag(OUTPUT_VALUE) is None
                        ):
                            LLMObs.annotate(span=span, output_data=resp)
                        return resp

            return wrapper

        if original_func and callable(original_func):
            return inner(original_func)
        return inner

    return decorator


llm = _model_decorator("llm")
embedding = _model_decorator("embedding")
workflow = _llmobs_decorator("workflow")
task = _llmobs_decorator("task")
tool = _llmobs_decorator("tool")
retrieval = _llmobs_decorator("retrieval")
agent = _llmobs_decorator("agent")
