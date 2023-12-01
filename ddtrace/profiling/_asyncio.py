# -*- encoding: utf-8 -*-
from functools import partial
import sys
from types import ModuleType  # noqa:F401
import typing  # noqa:F401

from ddtrace.internal.module import ModuleWatchdog
from ddtrace.internal.utils import get_argument_value
from ddtrace.internal.wrapping import wrap

from . import _threading


THREAD_LINK = None  # type: typing.Optional[_threading._ThreadLink]


def current_task(loop=None):
    return None


def all_tasks(loop=None):
    return []


def _task_get_name(task):
    return "Task-%d" % id(task)


@ModuleWatchdog.after_module_imported("asyncio")
def _(asyncio):
    # type: (ModuleType) -> None
    global THREAD_LINK

    if hasattr(asyncio, "current_task"):
        globals()["current_task"] = asyncio.current_task
    elif hasattr(asyncio.Task, "current_task"):
        globals()["current_task"] = asyncio.Task.current_task

    if hasattr(asyncio, "all_tasks"):
        globals()["all_tasks"] = asyncio.all_tasks
    elif hasattr(asyncio.Task, "all_tasks"):
        globals()["all_tasks"] = asyncio.Task.all_tasks

    if hasattr(asyncio.Task, "get_name"):
        # `get_name` is only available in Python ≥ 3.8
        globals()["_task_get_name"] = lambda task: task.get_name()

    if THREAD_LINK is None:
        THREAD_LINK = _threading._ThreadLink()

    @partial(wrap, sys.modules["asyncio.events"].BaseDefaultEventLoopPolicy.set_event_loop)
    def _(f, args, kwargs):
        try:
            return f(*args, **kwargs)
        finally:
            THREAD_LINK.clear_threads(set(sys._current_frames().keys()))
            loop = get_argument_value(args, kwargs, 1, "loop")
            if loop is not None:
                THREAD_LINK.link_object(loop)


def get_event_loop_for_thread(thread_id):
    global THREAD_LINK

    return THREAD_LINK.get_object(thread_id) if THREAD_LINK is not None else None
