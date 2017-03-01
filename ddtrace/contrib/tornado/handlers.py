from wrapt import function_wrapper


@function_wrapper
def wrapper_on_finish(func, handler, args, kwargs):
    """
    Wrapper for ``on_finish`` method of a ``RequestHandler``. This is
    the last executed method after the response has been sent.
    In this callback we try to retrieve and close the current request
    root span.
    """
    request_span = getattr(handler.request, '__datadog_request_span', None)
    if request_span:
        request_span.finish()

    return func(*args, **kwargs)
