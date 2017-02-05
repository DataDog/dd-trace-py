from wrapt import function_wrapper

from ddtrace.contrib.tornado import tracer


@function_wrapper
def wrapper_on_finish(func, module, args, kwargs):
    """
    TODO
    """
    ctx = tracer.get_call_context()
    # TODO: we may not have it!
    ctx._request_span.finish()
    return func(*args, **kwargs)
