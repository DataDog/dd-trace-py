"""
This module includes a list of convenience methods that
can be used to simplify some operations while handling
Context and Spans in instrumented ``asyncio`` code.
"""
import asyncio

# TODO: we don't want to do this; it will be
# from ddtrace import tracer
from ddtrace.contrib.asyncio import tracer


def ensure_future(coro_or_future, *, loop=None):
    """
    Wrapper for the asyncio.ensure_future() function that
    sets a context to the newly created Task. If the current
    task already has a Context, it will be attached to the
    new Task so the Trace list will be preserved.
    """
    current_ctx = tracer.get_call_context()
    task = asyncio.ensure_future(coro_or_future, loop=loop)
    setattr(task, '__datadog_context', current_ctx)
    return task


def run_in_executor():
    """
    This wrapper must be implemented.
    The idea is that when you run synchronous code in a separated
    executor, a copy of the context will be available in the new Thread.
    After the thread has been executed, the Context can be merged back
    if it has been used.

    TODO: we're not providing this API at the moment and run_in_executor
    will not work with the current asyncio tracing API. The implementation
    is in the roadmap after frameworks instrumentation.
    Probably this requires that Tracer is merged with AsyncTracer.
    """
    pass
