import asyncio
import sys

from ddtrace.vendor.wrapt import ObjectProxy
from ddtrace.vendor.wrapt import wrap_function_wrapper as _w

from ..trace_utils import unwrap as _u
from .wrappers import wrapped_create_task


def patch():
    """Patches current loop `create_task()` method to enable spawned tasks to
    parent to the base task context.
    """
    if getattr(asyncio, "_datadog_patch", False):
        return
    setattr(asyncio, "_datadog_patch", True)

    if sys.version_info < (3, 7, 0):
        _w(asyncio.BaseEventLoop, "create_task", wrapped_create_task)

        # also patch event loop if not inheriting the wrapped create_task from BaseEventLoop
        loop = asyncio.get_event_loop()
        if not isinstance(loop.create_task, ObjectProxy):
            _w(loop, "create_task", wrapped_create_task)


def unpatch():
    """Remove tracing from patched modules."""

    if getattr(asyncio, "_datadog_patch", False):
        setattr(asyncio, "_datadog_patch", False)

        if sys.version_info < (3, 7, 0):
            _u(asyncio.BaseEventLoop, "create_task")

            # also unpatch event loop if not inheriting the already unwrapped create_task from BaseEventLoop
            loop = asyncio.get_event_loop()
            if isinstance(loop.create_task, ObjectProxy):
                _u(loop, "create_task")
