from functools import wraps
import inspect

from ddtrace.internal.logger import get_logger
from ddtrace.llmobs import LLMObs


log = get_logger(__name__)


def llm(model_name, model_provider=None, name=None, session_id=None):
    def inner(func):
        span_name = name
        if span_name is None:
            span_name = func.__name__

        @wraps(func)
        def gen_wrapper(*args, **kwargs):
            gen = func(*args, **kwargs)

            if not LLMObs.enabled or LLMObs._instance is None:
                log.warning("LLMObs.llm() cannot be used while LLMObs is disabled.")
                for ret in gen:
                    try:
                        i = yield ret
                        if i:
                            gen.send(i)
                    except GeneratorExit:
                        gen.close()
                        return
                    except Exception as e:
                        gen.throw(e)
                return

            with LLMObs.llm(
                model_name=model_name, model_provider=model_provider, name=span_name, session_id=session_id
            ):
                for ret in gen:
                    try:
                        i = yield ret
                        if i:
                            gen.send(i)
                    except GeneratorExit:
                        gen.close()
                        return
                    except Exception as e:
                        gen.throw(e)

        @wraps(func)
        def wrapper(*args, **kwargs):
            if not LLMObs.enabled or LLMObs._instance is None:
                log.warning("LLMObs.llm() cannot be used while LLMObs is disabled.")
                return func(*args, **kwargs)
            with LLMObs.llm(
                model_name=model_name,
                model_provider=model_provider,
                name=span_name,
                session_id=session_id,
            ):
                return func(*args, **kwargs)

        return gen_wrapper if inspect.isgeneratorfunction(func) else wrapper

    return inner


def llmobs_decorator(operation_kind):
    def decorator(original_func=None, name=None, session_id=None):
        def inner(func):
            span_name = name
            if span_name is None:
                span_name = func.__name__

            @wraps(func)
            def gen_wrapper(*args, **kwargs):
                gen = func(*args, **kwargs)

                if not LLMObs.enabled or LLMObs._instance is None:
                    log.warning("LLMObs.{}() cannot be used while LLMObs is disabled.", operation_kind)
                    for ret in gen:
                        try:
                            i = yield ret
                            if i:
                                gen.send(i)
                        except GeneratorExit:
                            gen.close()
                            return
                        except Exception as e:
                            gen.throw(e)
                    return

                traced_operation = getattr(LLMObs, operation_kind, "workflow")
                with traced_operation(name=span_name, session_id=session_id):
                    for ret in gen:
                        try:
                            i = yield ret
                            if i:
                                gen.send(i)
                        except GeneratorExit:
                            gen.close()
                            return
                        except Exception as e:
                            gen.throw(e)

            @wraps(func)
            def wrapper(*args, **kwargs):
                if not LLMObs.enabled or LLMObs._instance is None:
                    log.warning("LLMObs.{}() cannot be used while LLMObs is disabled.", operation_kind)
                    return func(*args, **kwargs)
                traced_operation = getattr(LLMObs, operation_kind, "workflow")
                with traced_operation(name=span_name, session_id=session_id):
                    return func(*args, **kwargs)

            return gen_wrapper if inspect.isgeneratorfunction(func) else wrapper

        if original_func and callable(original_func):
            return inner(original_func)
        return inner

    return decorator


workflow = llmobs_decorator("workflow")
task = llmobs_decorator("task")
tool = llmobs_decorator("tool")
agent = llmobs_decorator("agent")
