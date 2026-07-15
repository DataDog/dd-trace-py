from cpython.ref cimport PyObject
from libc.stdint cimport uintptr_t

from wrapt.importer import when_imported

from ddtrace.internal.settings.profiling import config
from ddtrace.profiling import _asyncio

_gevent_helper = None
_gevent_support_initialized = False


def _initialize_gevent_module(gevent):
    global _gevent_helper
    from ddtrace.profiling import _gevent

    _gevent_helper = _gevent
    if config.stack.enabled:
        _gevent.patch()


cpdef initialize_gevent_support():
    global _gevent_support_initialized

    if _gevent_support_initialized:
        return
    _gevent_support_initialized = True
    when_imported("gevent")(_initialize_gevent_module)


cpdef uintptr_t task_object_address(object task):
    """Return the memory address of a Python object as a numeric task ID."""
    return <uintptr_t><PyObject*>task


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
            task_id = task_object_address(task)
            task_name = _asyncio._task_get_name(task)
            frame = _asyncio_task_get_frame(task)

    # gevent fallback:
    # - if both gevent and asyncio are in use, asyncio task metadata wins
    # - this path works for both stack and non-stack profiling modes
    if task_id is None and _gevent_helper is not None:
        task_id, task_name, frame = _gevent_helper.get_current_greenlet_task()

    return task_id, task_name, frame
