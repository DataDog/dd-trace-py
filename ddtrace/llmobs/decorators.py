from functools import wraps
from typing import Callable
from typing import Optional

from ddtrace.internal.logger import get_logger
from ddtrace.llmobs import LLMObs


log = get_logger(__name__)


def llm(
    model_name: str,
    model_provider: Optional[str] = None,
    name: Optional[str] = None,
    session_id: Optional[str] = None,
    ml_app: Optional[str] = None,
):
    def inner(func):
        @wraps(func)
        def wrapper(*args, **kwargs):
            if not LLMObs.enabled or LLMObs._instance is None:
                log.warning("LLMObs.llm() cannot be used while LLMObs is disabled.")
                return func(*args, **kwargs)
            span_name = name
            if span_name is None:
                span_name = func.__name__
            with LLMObs.llm(
                model_name=model_name,
                model_provider=model_provider,
                name=span_name,
                session_id=session_id,
                ml_app=ml_app,
            ):
                return func(*args, **kwargs)

        return wrapper

    return inner


def llmobs_decorator(operation_kind):
    def decorator(
        original_func: Optional[Callable] = None,
        name: Optional[str] = None,
        session_id: Optional[str] = None,
        ml_app: Optional[str] = None,
    ):
        def inner(func):
            @wraps(func)
            def wrapper(*args, **kwargs):
                if not LLMObs.enabled or LLMObs._instance is None:
                    log.warning("LLMObs.{}() cannot be used while LLMObs is disabled.", operation_kind)
                    return func(*args, **kwargs)
                span_name = name
                if span_name is None:
                    span_name = func.__name__
                traced_operation = getattr(LLMObs, operation_kind, "workflow")
                with traced_operation(name=span_name, session_id=session_id, ml_app=ml_app):
                    return func(*args, **kwargs)

            return wrapper

        if original_func and callable(original_func):
            return inner(original_func)
        return inner

    return decorator


workflow = llmobs_decorator("workflow")
task = llmobs_decorator("task")
tool = llmobs_decorator("tool")
agent = llmobs_decorator("agent")
