from types import ModuleType
import weakref

from wrapt.importer import when_imported

from .. import _asyncio
from .. import _threading


_gevent_tracer = None


@when_imported("gevent")
def install_greenlet_tracer(gevent):
    global _gevent_tracer

    try:
        import gevent.hub
        import gevent.thread
        from greenlet import getcurrent
        from greenlet import greenlet
        from greenlet import settrace
    except ImportError:
        # We don't seem to have the required dependencies.
        return

    class DDGreenletTracer(object):
        def __init__(self, gevent):
            # type: (ModuleType) -> None
            self.gevent = gevent

            self.previous_trace_function = settrace(self)
            self.greenlets = weakref.WeakValueDictionary()
            self.active_greenlet = getcurrent()
            self._store_greenlet(self.active_greenlet)

        def _store_greenlet(
                self,
                greenlet,  # type: greenlet.greenlet
        ):
            # type: (...) -> None
            self.greenlets[gevent.thread.get_ident(greenlet)] = greenlet

        def __call__(self, event, args):
            if event in ('switch', 'throw'):
                # Do not trace gevent Hub: the Hub is a greenlet but we want to know the latest active greenlet *before*
                # the application yielded back to the Hub. There's no point showing the Hub most of the time to the
                # users as that does not give any information about user code.
                if not isinstance(args[1], gevent.hub.Hub):
                    self.active_greenlet = args[1]
                    self._store_greenlet(args[1])

            if self.previous_trace_function is not None:
                self.previous_trace_function(event, args)

    _gevent_tracer = DDGreenletTracer(gevent)


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

    # gevent greenlet support:
    # - we only support tracing tasks in the greenlets run in the MainThread.
    # - if both gevent and asyncio are in use (!) we only return asyncio
    if task_id is None and _gevent_tracer is not None:
        gevent_thread = _gevent_tracer.gevent.thread
        task_id = gevent_thread.get_ident(_gevent_tracer.active_greenlet)
        # Greenlets might be started as Thread in gevent
        task_name = _threading.get_thread_name(task_id)
        frame = _gevent_tracer.active_greenlet.gr_frame

    return task_id, task_name, frame


cpdef list_tasks(thread_id):
    # type: (...) -> typing.List[typing.Tuple[int, str, types.FrameType]]
    """Return the list of running tasks.

    This is computed for gevent by taking the list of existing threading.Thread object and removing if any real OS
    thread that might be running.

    :return: [(task_id, task_name, task_frame), ...]"""

    tasks = []

    if _gevent_tracer is not None:
        if type(_threading.get_thread_by_id(thread_id)).__name__.endswith("_MainThread"):
            # Under normal circumstances, the Hub is running in the main thread.
            # Python will only ever have a single instance of a _MainThread
            # class, so if we find it we attribute all the greenlets to it.
            tasks.extend(
                [
                    (
                        greenlet_id,
                        _threading.get_thread_name(greenlet_id),
                        greenlet.gr_frame
                    )
                    for greenlet_id, greenlet in dict(_gevent_tracer.greenlets).items()
                    if not greenlet.dead
                ]
            )

    loop = _asyncio.get_event_loop_for_thread(thread_id)
    if loop is not None:
        tasks.extend([
            (id(task),
                _asyncio._task_get_name(task),
                _asyncio_task_get_frame(task))
            for task in _asyncio.all_tasks(loop)
        ])

    return tasks
