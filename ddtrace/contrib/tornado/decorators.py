import sys
import ddtrace

from .constants import FUTURE_SPAN_KEY
from .stack_context import TracerStackContext


def _finish_span(future):
    """
    Finish the span if it's attached to the given ``Future`` object.
    This method is a Tornado callback used to close a decorated function
    executed as a coroutine or as a synchronous function in another thread.
    """
    span = getattr(future, FUTURE_SPAN_KEY, None)

    if span:
        if callable(getattr(future, 'exc_info', None)):
            # retrieve the exception from the coroutine object
            exc_info = future.exc_info()
            if exc_info:
                span.set_exc_info(*exc_info)
        elif callable(getattr(future, 'exception', None)):
            # retrieve the exception from the Future object
            # that is executed in a different Thread
            if future.exception():
                span.set_exc_info(*sys.exc_info())

        span.finish()


def _run_on_executor(run_on_executor, _, params, kw_params):
    """
    Wrap the `run_on_executor` function so that when a function is executed
    in a different thread, we use an intermediate function (and a closure)
    to keep track of the current `parent_span` if any. The real function
    is then executed in a `TracerStackContext` so that `tracer.trace()`
    can be used as usual, both with empty or existing `Context`.
    """
    # this is the original call that returns a decorator; invoked once,
    # it's used as a sanity check that may return exceptions as
    # expected in Tornado's code
    decorator = run_on_executor(*params, **kw_params)

    # if `run_on_executor` is called without arguments, the
    # function returns itself; here we mimic the same action
    # returning this decorator
    if decorator.__module__ == 'tornado.concurrent':
        return decorator

    # closure that holds the parent_span of this logical execution; the
    # Context object may not exist and/or may be empty
    current_ctx = ddtrace.tracer.get_call_context()
    parent_span = getattr(current_ctx, '_current_span', None)

    def traced_wrapper(*args, **kwargs):
        """
        This intermediate function is executed in the newly created thread. Here
        using a `TracerStackContext` is legit because this function doesn't interfere
        with the main thread loop. `StackContext` states, used as a carrier for our Context
        object, are thread-local so retrieving the context here will always bring to an
        empty `Context`.
        """
        with TracerStackContext():
            # the function that must be called in the executor
            fn = params[0]
            ctx = ddtrace.tracer.get_call_context()
            ctx._current_span = parent_span

            return fn(*args, **kwargs)

    # return our wrapper function that executes an intermediate function to
    # trace the real execution in a different thread
    return run_on_executor(traced_wrapper)


def wrap_executor(tracer, fn, args, kwargs, span_name, service=None, resource=None, span_type=None):
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

        # duck-typing: if it has `add_done_callback` it's a Future
        # object whatever is the underlying implementation
        if callable(getattr(future, 'add_done_callback', None)):
            setattr(future, FUTURE_SPAN_KEY, span)
            future.add_done_callback(_finish_span)
        else:
            # TODO: it's a normal span
            span.finish()
    except Exception:
        span.set_traceback()
        span.finish()
        raise

    return future
