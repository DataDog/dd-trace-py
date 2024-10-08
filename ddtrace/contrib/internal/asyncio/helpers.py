"""
This module includes a list of convenience methods that
can be used to simplify some operations while handling
Context and Spans in instrumented ``asyncio`` code.
"""
import asyncio

import ddtrace
from ddtrace.contrib.internal.asyncio.provider import AsyncioContextProvider
from ddtrace.contrib.internal.asyncio.wrappers import wrapped_create_task
from ddtrace.vendor.debtcollector import deprecate


def set_call_context(task, ctx):
    """
    Updates the ``Context`` for the given Task. Useful when you need to
    pass the context among different tasks.

    This method is available for backward-compatibility. Use the
    ``AsyncioContextProvider`` API to set the current active ``Context``.
    """
    deprecate(
        "ddtrace.contrib.internal.asyncio.set_call_context(..) is deprecated. "
        "The ddtrace library fully supports propagating "
        "trace contextes to async tasks. No additional configurations are required.",
        version="3.0.0",
    )
    setattr(task, AsyncioContextProvider._CONTEXT_ATTR, ctx)


def ensure_future(coro_or_future, *, loop=None, tracer=None):
    """Wrapper that sets a context to the newly created Task.

    If the current task already has a Context, it will be attached to the new Task so the Trace list will be preserved.
    """
    deprecate(
        "ddtrace.contrib.internal.asyncio.ensure_future(..) is deprecated. "
        "The ddtrace library fully supports propagating "
        "trace contextes to async tasks. No additional configurations are required.",
        version="3.0.0",
    )
    tracer = tracer or ddtrace.tracer
    current_ctx = tracer.current_trace_context()
    task = asyncio.ensure_future(coro_or_future, loop=loop)
    set_call_context(task, current_ctx)
    return task


def run_in_executor(loop, executor, func, *args, tracer=None):
    """Wrapper function that sets a context to the newly created Thread.

    If the current task has a Context, it will be attached as an empty Context with the current_span activated to
    inherit the ``trace_id`` and the ``parent_id``.

    Because the Executor can run the Thread immediately or after the
    coroutine is executed, we may have two different scenarios:
    * the Context is copied in the new Thread and the trace is sent twice
    * the coroutine flushes the Context and when the Thread copies the
    Context it is already empty (so it will be a root Span)

    To support both situations, we create a new Context that knows only what was
    the latest active Span when the new thread was created. In this new thread,
    we fallback to the thread-local ``Context`` storage.

    """
    deprecate(
        "ddtrace.contrib.internal.asyncio.run_in_executor(..) is deprecated. "
        "The ddtrace library fully supports propagating "
        "trace contexts to async tasks. No additional configurations are required.",
        version="3.0.0",
    )
    tracer = tracer or ddtrace.tracer
    current_ctx = tracer.current_trace_context()

    # prepare the future using an executor wrapper
    future = loop.run_in_executor(executor, _wrap_executor, func, args, tracer, current_ctx)
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
    tracer.context_provider.activate(ctx)
    return fn(*args)


def create_task(*args, **kwargs):
    """This function spawns a task with a Context that inherits the
    `trace_id` and the `parent_id` from the current active one if available.
    """
    deprecate(
        "ddtrace.contrib.internal.asyncio.create_task(..) is deprecated. "
        "The ddtrace library fully supports propagating "
        "trace contexts to async tasks. No additional configurations are required.",
        version="3.0.0",
    )
    loop = asyncio.get_event_loop()
    return wrapped_create_task(loop.create_task, None, args, kwargs)
