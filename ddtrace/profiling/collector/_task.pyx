from wrapt.importer import when_imported

from .. import _asyncio
from ddtrace.internal.settings.profiling import config

_gevent_helper = None

@when_imported("gevent")
def _(gevent):
    global _gevent_helper
    from .. import _gevent

    _gevent_helper = _gevent
    if config.stack.enabled:
        _gevent.patch()


cdef _asyncio_task_get_frame(task):
    coro = task._coro
    if hasattr(coro, "cr_frame"):
        # async def
        return coro.cr_frame
    elif hasattr(coro, "gi_frame"):
        # legacy coroutines
        return coro.gi_frame
    elif hasattr(coro, "ag_frame"):
        # async generators
        return coro.ag_frame
    # unknown
    return None


cpdef get_task():
    """Return the task id, name, and frame for the current task."""
    task_id = None
    task_name = None
    frame = None

    if _asyncio.get_running_loop() is not None:
        task = _asyncio.current_task()
        if task is not None:
            task_id = id(task)
            task_name = _asyncio._task_get_name(task)
            frame = _asyncio_task_get_frame(task)

    # gevent fallback:
    # - if both gevent and asyncio are in use, asyncio task metadata wins
    # - this path works for both stack and non-stack profiling modes
    if task_id is None and _gevent_helper is not None:
        task_id, task_name, frame = _gevent_helper.get_current_greenlet_task()

    return task_id, task_name, frame
