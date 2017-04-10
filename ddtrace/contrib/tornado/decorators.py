import sys
import ddtrace

from functools import wraps

from .constants import FUTURE_SPAN_KEY, PARENT_SPAN_KEY
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
    in a different thread, we pass the current parent Span to the intermediate
    function that will execute the original call. The original function
    is then executed within a `TracerStackContext` so that `tracer.trace()`
    can be used as usual, both with empty or existing `Context`.
    """
    def pass_context_decorator(fn):
        """
        Decorator that is used to wrap the original `run_on_executor_decorator`
        so that we can pass the current active context before the `executor.submit`
        is called. In this case we get the `parent_span` reference and we pass
        that reference to `fn` reference. Because in the outer wrapper we replace
        the original call with our `traced_wrapper`, we're sure that the `parent_span`
        is passed to our intermediate function and not to the user function.
        """
        @wraps(fn)
        def wrapper(*args, **kwargs):
            # from the current context, retrive the active span
            current_ctx = ddtrace.tracer.get_call_context()
            parent_span = getattr(current_ctx, '_current_span', None)

            # pass the current parent span in the Future call so that
            # it can be retrieved later
            kwargs.update({PARENT_SPAN_KEY: parent_span})
            return fn(*args, **kwargs)
        return wrapper

    # we expect exceptions here if the `run_on_executor` is called with
    # wrong arguments; in that case we should not do anything because
    # the exception must not be handled here
    decorator = run_on_executor(*params, **kw_params)

    # `run_on_executor` can be called with arguments; in this case we
    # return an inner decorator that holds the real function that should be
    # called
    if decorator.__module__ == 'tornado.concurrent':
        def run_on_executor_decorator(deco_fn):
            def inner_traced_wrapper(*args, **kwargs):
                # retrieve the parent span from the function kwargs
                parent_span = kwargs.pop(PARENT_SPAN_KEY, None)
                return run_executor_stack_context(deco_fn, args, kwargs, parent_span)
            return pass_context_decorator(decorator(inner_traced_wrapper))

        return run_on_executor_decorator

    # return our wrapper function that executes an intermediate function to
    # trace the real execution in a different thread
    def traced_wrapper(*args, **kwargs):
        # retrieve the parent span from the function kwargs
        parent_span = kwargs.pop(PARENT_SPAN_KEY, None)
        return run_executor_stack_context(params[0], args, kwargs, parent_span)

    return pass_context_decorator(run_on_executor(traced_wrapper))


def run_executor_stack_context(fn, args, kwargs, parent_span):
    """
    This intermediate function is always executed in a newly created thread. Here
    using a `TracerStackContext` is legit because this function doesn't interfere
    with the main thread loop. `StackContext` states are thread-local and retrieving
    the context here will always bring to an empty `Context`.
    """
    with TracerStackContext():
        ctx = ddtrace.tracer.get_call_context()
        ctx._current_span = parent_span
        return fn(*args, **kwargs)


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
            # we don't have a future so the `future` variable
            # holds the result of the function
            span.finish()
    except Exception:
        span.set_traceback()
        span.finish()
        raise

    return future
