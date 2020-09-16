import uvloop
from ddtrace.vendor.wrapt import wrap_function_wrapper as _w

from ...compat import CONTEXTVARS_IS_AVAILABLE
from ...utils.wrappers import unwrap as _u
from ..asyncio.wrappers import wrapped_create_task, wrapped_create_task_contextvars


def patch():
    """Patches current loop `create_task()` method to enable spawned tasks to
    parent to the base task context.
    """
    if getattr(uvloop, "_datadog_patch", False):
        return
    setattr(uvloop, "_datadog_patch", True)

    _w(
        uvloop,
        "Loop.create_task",
        wrapped_create_task_contextvars if CONTEXTVARS_IS_AVAILABLE else wrapped_create_task,
    )


def unpatch():
    """Remove tracing from patched modules."""

    if getattr(uvloop, "_datadog_patch", False):
        setattr(uvloop, "_datadog_patch", False)

    _u(uvloop.Loop, "create_task")
