import asyncio

from ddtrace.vendor.wrapt import ObjectProxy
from ddtrace.vendor.wrapt import wrap_function_wrapper as _w

from ...compat import CONTEXTVARS_IS_AVAILABLE
from ...utils.wrappers import unwrap as _u
from .wrappers import wrapped_create_task
from .wrappers import wrapped_create_task_contextvars


def patch():
    """Patches current loop `create_task()` method to enable spawned tasks to
    parent to the base task context.
    """
    if getattr(asyncio, "_datadog_patch", False):
        return
    setattr(asyncio, "_datadog_patch", True)

    wrapper = wrapped_create_task_contextvars if CONTEXTVARS_IS_AVAILABLE else wrapped_create_task

    _w(asyncio.BaseEventLoop, "create_task", wrapper)

    # also patch event loop if not inheriting the wrapped create_task from BaseEventLoop
    loop = asyncio.get_event_loop()
    if not isinstance(loop.create_task, ObjectProxy):
        _w(loop, "create_task", wrapper)


def unpatch():
    """Remove tracing from patched modules."""

    if getattr(asyncio, "_datadog_patch", False):
        setattr(asyncio, "_datadog_patch", False)

    _u(asyncio.BaseEventLoop, "create_task")

    # also unpatch event loop if not inheriting the already unwrapped create_task from BaseEventLoop
    loop = asyncio.get_event_loop()
    if isinstance(loop.create_task, ObjectProxy):
        _u(loop, "create_task")
