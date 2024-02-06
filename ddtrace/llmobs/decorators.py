import functools


def llmobs_decorator(operation_kind):
    def decorator(original_func=None, name=operation_kind, **user_kwargs):
        def inner(func):
            @functools.wraps(func)
            def wrapper(*args, **kwargs):
                from ddtrace.llmobs import llmobs_instance
                traced_operation = getattr(llmobs_instance, operation_kind, "llm")
                with traced_operation(name=name, **user_kwargs):
                    # TODO: capture input here?
                    result = func(*args, **kwargs)
                    # TODO: capture output here?
                    return result
            return wrapper
        if original_func and callable(original_func):
            return inner(original_func)
        return inner
    return decorator


workflow = llmobs_decorator("workflow")
agent = llmobs_decorator("agent")
task = llmobs_decorator("task")
tool = llmobs_decorator("tool")
llm = llmobs_decorator("llm")
