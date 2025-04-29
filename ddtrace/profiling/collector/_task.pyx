import sys
from types import ModuleType
import weakref

from wrapt.importer import when_imported

from .. import _asyncio
from .. import _threading



@when_imported("gevent")
def _(gevent):
    from .. import _gevent

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


cpdef get_task(thread_id):
    """Return the task id and name for a thread."""
    task_id = None
    task_name = None
    frame = None

    loop = _asyncio.get_event_loop_for_thread(thread_id)
    if loop is not None:
        task = _asyncio.current_task(loop)
        if task is not None:
            task_id = id(task)
            task_name = _asyncio._task_get_name(task)
            frame = _asyncio_task_get_frame(task)

    return task_id, task_name, frame


cpdef list_tasks(thread_id):
    # type: (...) -> typing.List[typing.Tuple[int, str, types.FrameType]]
    """Return the list of running tasks.

    This is computed for gevent by taking the list of existing threading.Thread object and removing if any real OS
    thread that might be running.

    :return: [(task_id, task_name, task_frame), ...]"""

    tasks = []

    loop = _asyncio.get_event_loop_for_thread(thread_id)
    if loop is not None:
        tasks.extend([
            (id(task),
                _asyncio._task_get_name(task),
                _asyncio_task_get_frame(task))
            for task in _asyncio.all_tasks(loop)
        ])

    return tasks
