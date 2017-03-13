from tornado.concurrent import Future

from .settings import FUTURE_SPAN_KEY


def _finish_coroutine_span(future):
    """
    Finish the opened span if it's attached to the given ``Future``
    object. This method is a Tornado callback, that is used to close
    a decorated coroutine.
    """
    span = getattr(future, FUTURE_SPAN_KEY, None)
    if span:
        # retrieve the exception info from the Future (if any)
        exc_info = future.exc_info()
        if exc_info:
            span.set_exc_info(*exc_info)

        span.finish()

def wrap_executor(tracer, fn, args, kwargs, span_name, service, resource, span_type):
    """
    Wrap executor function used to change the default behavior of
    ``Tracer.wrap()`` method. A decorated Tornado function can be
    a regular function or a coroutine; if a coroutine is decorated, a
    span is attached to the returned ``Future`` and a callback is set
    so that it will close the span when the ``Future`` is done.
    """
    span = tracer.trace(span_name, service=service, resource=resource, span_type=span_type)

    # catch standard exceptions raised in synchronous executions
    try:
        future = fn(*args, **kwargs)
    except Exception:
        span.set_traceback()
        span.finish()
        raise

    # attach the tracing span if it's a future
    if isinstance(future, Future):
        setattr(future, FUTURE_SPAN_KEY, span)
        future.add_done_callback(_finish_coroutine_span)
    else:
        span.finish()
    return future
