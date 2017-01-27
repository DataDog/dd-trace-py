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


# local storage used in the single-threaded async loop
# TODO: we may have multiple threads with multiple independent loops
_local = threading.local()

# TODO: this causes a memory leak if contexts are not removed
# when they're finished (and flushed); we may want to use
# weak references (like a weak key dictionary), OR remove
# the context in the reset() method if AsyncContext is developed.
_local.contexts = {}


def get_call_context(loop=None):
    """
    Returns the scoped context for this execution flow.
    """
    # TODO: this may raise exceptions; provide defaults or
    # gracefully log errors
    loop = loop or asyncio.get_event_loop()

    # the current unit of work (if tasks are used)
    # TODO: it may return None
    task = asyncio.Task.current_task(loop=loop)

    try:
        # return the active Context for this task
        return _local.contexts[task]
    except (AttributeError, LookupError):
        # create a new Context if it's not available
        # TODO: we may not want to create Context everytime
        ctx = Context()
        _local.contexts[task] = ctx
        return ctx
