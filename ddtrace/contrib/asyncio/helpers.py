"""
This module includes a list of convenience methods that
can be used to simplify some operations while handling
Context and Spans in instrumented ``asyncio`` code.
"""
import asyncio
import ddtrace

from .provider import CONTEXT_ATTR
from .compat import asyncio_current_task
from ...context import Context


def set_call_context(task, ctx):
    """
    Updates the ``Context`` for the given Task. Useful when you need to
    pass the context among different tasks.

    This method is available for backward-compatibility. Use the
    ``AsyncioContextProvider`` API to set the current active ``Context``.
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


def run_in_executor(loop, executor, func, *args, tracer=None):
    """
    Wrapper for the loop.run_in_executor() function that
    sets a context to the newly created Thread. If the current
    task has a Context, it will be attached as an empty Context
    with the current_span activated to inherit the ``trace_id``
    and the ``parent_id``.

    Because the Executor can run the Thread immediately or after the
    coroutine is executed, we may have two different scenarios:
    * the Context is copied in the new Thread and the trace is sent twice
    * the coroutine flushes the Context and when the Thread copies the
      Context it is already empty (so it will be a root Span)

    To support both situations, we create a new Context that knows only what was
    the latest active Span when the new thread was created. In this new thread,
    we fallback to the thread-local ``Context`` storage.
    """
    tracer = tracer or ddtrace.tracer
    ctx = Context()
    current_ctx = tracer.get_call_context()
    ctx._current_span = current_ctx._current_span

    # prepare the future using an executor wrapper
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
    tracer.context_provider.activate(ctx)
    return fn(*args)


def create_task(*args, **kwargs):
    """This function spawns a task with a Context that inherits the
    `trace_id` and the `parent_id` from the current active one if available.
    """
    loop = asyncio.get_event_loop()
    return _wrapped_create_task(loop.create_task, None, args, kwargs)


def _wrapped_create_task(wrapped, instance, args, kwargs):
    """Wrapper for ``create_task(coro)`` that propagates the current active
    ``Context`` to the new ``Task``. This function is useful to connect traces
    of detached executions.

    Note: we can't just link the task contexts due to the following scenario:
        * begin task A
        * task A starts task B1..B10
        * finish task B1-B9 (B10 still on trace stack)
        * task A starts task C
        * now task C gets parented to task B10 since it's still on the stack,
          however was not actually triggered by B10
    """
    new_task = wrapped(*args, **kwargs)
    current_task = asyncio_current_task()

    ctx = getattr(current_task, CONTEXT_ATTR, None)
    if ctx:
        # current task has a context, so parent a new context to the base context
        new_ctx = Context(
            trace_id=ctx.trace_id,
            span_id=ctx.span_id,
            sampling_priority=ctx.sampling_priority,
        )
        set_call_context(new_task, new_ctx)

    return new_task


def _wrapped_create_task_contextvars(wrapped, instance, args, kwargs):
    """Wrapper for ``create_task(coro)`` that propagates the current active
    ``Context`` to the new ``Task``. This function is useful to connect traces
    of detached executions. Uses contextvars for task-local storage.
    """
    current_task_ctx = ddtrace.tracer.get_call_context()

    if not current_task_ctx:
        # no current context exists so nothing special to be done in handling
        # context for new task
        return wrapped(*args, **kwargs)

    # clone and activate current task's context for new task to support
    # detached executions
    new_task_ctx = current_task_ctx.clone()
    ddtrace.tracer.context_provider.activate(new_task_ctx)
    try:
        # activated context will now be copied to new task
        return wrapped(*args, **kwargs)
    finally:
        # reactivate current task context
        ddtrace.tracer.context_provider.activate(current_task_ctx)
