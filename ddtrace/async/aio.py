"""
This module is highly experimental and should not be used
in real application. Monkey patching here is used only for
convenience. This will not be the final public API.

This module import will fail in Python 2 because no support
will be provided for deprecated async ports.
"""
import asyncio
import threading

from ..context import Context


def get_call_context(loop=None):
    """
    Returns the scoped context for this execution flow. The Context
    is attached in the active Task; we can use it as Context carrier.

    NOTE: because the Context is attached to a Task, the Garbage Collector
    frees both the Task and the Context without causing a memory leak.
    """
    # TODO: this may raise exceptions; provide defaults or
    # gracefully log errors
    loop = loop or asyncio.get_event_loop()

    # the current unit of work (if tasks are used)
    task = asyncio.Task.current_task(loop=loop)
    if task is None:
        # FIXME: it will not work here
        # if the Task is None, the application will crash with unhandled exception
        # if we return a Context(), we will attach this Context
        return

    try:
        # return the active Context for this task (if any)
        return task.__datadog_context
    except (KeyError, AttributeError):
        # create a new Context if it's not available
        # TODO: we may not want to create Context everytime
        ctx = Context()
        task.__datadog_context = ctx
        return ctx


def set_call_context(task, ctx):
    """
    Updates the Context for the given Task. Useful when you need to
    pass the context among different tasks.
    """
    task.__datadog_context = ctx
