"""
This module includes a list of convenience methods that
can be used to simplify some operations while handling
Context and Spans in instrumented code.
"""
import asyncio

from . import aio


def ensure_future(coro_or_future, *, loop=None):
    """
    Wrapper for the asyncio.ensure_future() function that
    sets a context to the newly created Task. If the current
    task already has a Context, it will be attached to the
    new Task so the Trace list will be preserved.
    """
    # TODO: a lot of things may fail in complex application; sanity checks
    # and stability issues will be solved later
    current_ctx = aio.get_call_context()
    task = asyncio.ensure_future(coro_or_future, loop=loop)
    aio.set_call_context(task, current_ctx)
    return task
