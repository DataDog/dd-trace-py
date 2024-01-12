import asyncio


def get_version():
    # type: () -> str
    return ""


def patch():
    """Patches current loop `create_task()` method to enable spawned tasks to
    parent to the base task context.
    """
    if getattr(asyncio, "_datadog_patch", False):
        return
    asyncio._datadog_patch = True


def unpatch():
    """Remove tracing from patched modules."""

    if getattr(asyncio, "_datadog_patch", False):
        asyncio._datadog_patch = False
