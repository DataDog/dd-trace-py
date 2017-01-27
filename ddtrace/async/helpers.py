"""
This module includes a list of convenience methods that
can be used to simplify some operations while handling
Context and Spans in instrumented code.
"""
import asyncio


def ensure_future(coroutine_or_future, *, loop=None):
    """
    Wrapper for the asyncio.ensure_future() function that
    sets a context to the newly created Task. If the current
    task already has a Context, it will be attached to the
    new Task so the Trace list will be preserved.
    """
