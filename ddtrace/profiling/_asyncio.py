# -*- encoding: utf-8 -*-
import sys
import typing


try:
    import asyncio
except ImportError:
    DefaultEventLoopPolicy = object

    def get_event_loop_policy():
        # type: (...) -> None
        return None

    def set_event_loop_policy(loop_policy):
        # type: (...) -> None
        pass

    asyncio_available = False

else:
    asyncio_available = True
    DefaultEventLoopPolicy = asyncio.DefaultEventLoopPolicy  # type: ignore[misc]

    get_event_loop_policy = asyncio.get_event_loop_policy  # type: ignore[assignment]
    set_event_loop_policy = asyncio.set_event_loop_policy  # type: ignore[assignment]

    if hasattr(asyncio, "current_task"):
        current_task = asyncio.current_task
    elif hasattr(asyncio.Task, "current_task"):
        current_task = asyncio.Task.current_task  # type: ignore[attr-defined]
    else:

        def current_task(loop=None):
            return None

    if hasattr(asyncio, "all_tasks"):
        all_tasks = asyncio.all_tasks
    elif hasattr(asyncio.Task, "all_tasks"):
        all_tasks = asyncio.Task.all_tasks  # type: ignore[attr-defined]
    else:

        def all_tasks(loop=None):
            return []

    if hasattr(asyncio.Task, "get_name"):
        # `get_name` is only available in Python ≥ 3.8
        def _task_get_name(task):
            return task.get_name()

    else:

        def _task_get_name(task):
            return "Task-%d" % id(task)


from . import _threading


class DdtraceProfilerEventLoopPolicy(DefaultEventLoopPolicy):
    def __init__(self):
        # type: (...) -> None
        super(DdtraceProfilerEventLoopPolicy, self).__init__()
        self.loop_per_thread = _threading._ThreadLink()  # type: _threading._ThreadLink[asyncio.AbstractEventLoop]

    def _clear_threads(self):
        self.loop_per_thread.clear_threads(set(sys._current_frames().keys()))

    def set_event_loop(self, loop):
        super(DdtraceProfilerEventLoopPolicy, self).set_event_loop(loop)
        self._clear_threads()
        if loop is not None:
            self.loop_per_thread.link_object(loop)

    def _ddtrace_get_loop(self, thread_id):
        # type: (...) -> typing.Optional[asyncio.AbstractEventLoop]
        return self.loop_per_thread.get_object(thread_id)
