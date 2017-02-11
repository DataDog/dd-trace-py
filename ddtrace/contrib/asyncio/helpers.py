"""
This module includes a list of convenience methods that
can be used to simplify some operations while handling
Context and Spans in instrumented ``asyncio`` code.
"""
import asyncio
import ddtrace

from .provider import CONTEXT_ATTR
from ...context import Context, ThreadLocalContext


def set_call_context(task, ctx):
    """
    Updates the ``Context`` for the given Task. Useful when you need to
    pass the context among different tasks.
    """
    setattr(task, CONTEXT_ATTR, ctx)


def ensure_future(coro_or_future, *, loop=None, tracer=None):
    """
    Wrapper for the asyncio.ensure_future() function that
    sets a context to the newly created Task. If the current
    task already has a Context, it will be attached to the
    new Task so the Trace list will be preserved.
    """
    tracer = tracer or ddtrace.tracer
    current_ctx = tracer.get_call_context()
    task = asyncio.ensure_future(coro_or_future, loop=loop)
    set_call_context(task, current_ctx)
    return task


def run_in_executor(executor, func, *args, loop=None, tracer=None):
    """
    Wrapper for the loop.run_in_executor() function that
    sets a context to the newly created Thread. If the current
    task has a Context, it will be attached as an empty Context
    with the current_span activated to inherit the ``trace_id``
    and the ``parent_id``.

    Because the separated thread does synchronous execution, the
    tracer context provider fallbacks to the thread-local ``Context``
    storage.
    """
    try:
        # TODO: maybe the loop kwarg should be removed
        loop = loop or asyncio.get_event_loop()
    except RuntimeError:
        # this exception means that the run_in_executor is run in the
        # wrong loop; this should happen only in wrong call usage
        # TODO: here we can't do something better; it's the same as
        # calling:
        #   loop = None
        #   loop.run_in_executor(...)
        raise

    # because the Executor can run the Thread immediately or after the
    # coroutine is executed, we may have two different scenarios:
    # * the Context is copied in the new Thread and the trace is sent twice
    # * the coroutine flushes the Context and when the Thread copies the
    #   Context it is already empty (so it will be a root Span)
    # because of that we create a new Context that knows only what was
    # the latest active Span when the executor has been launched
    tracer = tracer or ddtrace.tracer
    ctx = Context()
    current_ctx = tracer.get_call_context()
    ctx._current_span = current_ctx._current_span

    future = loop.run_in_executor(executor, _wrap_executor, func, args, tracer, ctx)
    return future


def _wrap_executor(fn, args, tracer, ctx):
    """
    This function is executed in the newly created Thread so the right
    ``Context`` can be set in the thread-local storage. This operation
    is safe because the ``Context`` class is thread-safe and can be
    updated concurrently.
    """
    # the AsyncioContextProvider knows that this is a new thread
    # so it is legit to pass the Context in the thread-local storage;
    # fn() will be executed outside the asyncio loop as a synchronous code
    tracer._context_provider._local.set(ctx)
    return fn(*args)
