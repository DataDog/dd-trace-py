# -*- encoding: utf-8 -*-
from functools import partial
import sys
from types import ModuleType
import typing


if typing.TYPE_CHECKING:
    import asyncio
    import asyncio as aio

from ddtrace.internal._unpatched import _threading as ddtrace_threading
from ddtrace.internal.datadog.profiling import stack
from ddtrace.internal.module import ModuleWatchdog
from ddtrace.internal.settings.profiling import config
from ddtrace.internal.utils import get_argument_value
from ddtrace.internal.wrapping import wrap

from . import _threading


THREAD_LINK: typing.Optional["_threading._ThreadLink"] = None

ASYNCIO_IMPORTED: bool = False


def current_task(
    loop: typing.Optional["asyncio.AbstractEventLoop"] = None,
) -> typing.Optional["asyncio.Task[typing.Any]"]:
    return None


def all_tasks(
    loop: typing.Optional["asyncio.AbstractEventLoop"] = None,
) -> typing.List["asyncio.Task[typing.Any]"]:
    return []


def _task_get_name(task: "asyncio.Task[typing.Any]") -> str:
    return "Task-%d" % id(task)


def _call_init_asyncio(asyncio: ModuleType) -> None:
    from asyncio import tasks as asyncio_tasks

    if sys.hexversion >= 0x030C0000:
        scheduled_tasks = asyncio_tasks._scheduled_tasks.data  # type: ignore[attr-defined]
        eager_tasks = asyncio_tasks._eager_tasks  # type: ignore[attr-defined]
    else:
        scheduled_tasks = asyncio_tasks._all_tasks.data  # type: ignore[attr-defined]
        eager_tasks = None

    stack.init_asyncio(asyncio_tasks._current_tasks, scheduled_tasks, eager_tasks)  # type: ignore[attr-defined]


def link_existing_loop_to_current_thread() -> None:
    global ASYNCIO_IMPORTED

    # Only proceed if asyncio is actually imported and available
    # Don't rely solely on ASYNCIO_IMPORTED global since it persists across forks
    if not ASYNCIO_IMPORTED or "asyncio" not in sys.modules:
        return

    import asyncio

    # Only track if there's actually a running loop
    running_loop: typing.Optional["asyncio.AbstractEventLoop"] = None
    try:
        running_loop = asyncio.get_running_loop()
    except RuntimeError:
        # No existing loop to track, nothing to do
        return

    # We have a running loop, track it
    assert THREAD_LINK is not None  # nosec: assert is used for typing
    THREAD_LINK.clear_threads(set(sys._current_frames().keys()))
    THREAD_LINK.link_object(running_loop)
    stack.track_asyncio_loop(typing.cast(int, ddtrace_threading.current_thread().ident), running_loop)
    _call_init_asyncio(asyncio)


@ModuleWatchdog.after_module_imported("asyncio")
def _(asyncio: ModuleType) -> None:
    global THREAD_LINK
    global ASYNCIO_IMPORTED

    ASYNCIO_IMPORTED = True

    if hasattr(asyncio, "current_task"):
        globals()["current_task"] = asyncio.current_task
    elif hasattr(asyncio.Task, "current_task"):
        globals()["current_task"] = asyncio.Task.current_task

    if hasattr(asyncio, "all_tasks"):
        globals()["all_tasks"] = asyncio.all_tasks
    elif hasattr(asyncio.Task, "all_tasks"):
        globals()["all_tasks"] = asyncio.Task.all_tasks

    globals()["_task_get_name"] = lambda task: task.get_name()

    if THREAD_LINK is None:
        THREAD_LINK = _threading._ThreadLink()

    init_stack: bool = config.stack.enabled and stack.is_available

    # Python 3.14+: BaseDefaultEventLoopPolicy was renamed to _BaseDefaultEventLoopPolicy
    # Try both names for compatibility
    events_module = sys.modules["asyncio.events"]
    if sys.hexversion >= 0x030E0000:
        # Python 3.14+: Use _BaseDefaultEventLoopPolicy
        policy_class = getattr(events_module, "_BaseDefaultEventLoopPolicy", None)
    else:
        # Python < 3.14: Use BaseDefaultEventLoopPolicy
        policy_class = getattr(events_module, "BaseDefaultEventLoopPolicy", None)

    if policy_class is not None:

        @partial(wrap, policy_class.set_event_loop)
        def _(
            f: typing.Callable[..., typing.Any], args: tuple[typing.Any, ...], kwargs: dict[str, typing.Any]
        ) -> typing.Any:
            loop: typing.Optional["aio.AbstractEventLoop"] = get_argument_value(args, kwargs, 1, "loop")
            try:
                if init_stack:
                    stack.track_asyncio_loop(typing.cast(int, ddtrace_threading.current_thread().ident), loop)
                return f(*args, **kwargs)
            finally:
                assert THREAD_LINK is not None  # nosec: assert is used for typing
                THREAD_LINK.clear_threads(set(sys._current_frames().keys()))
                if loop is not None:
                    THREAD_LINK.link_object(loop)

    if init_stack:

        @partial(wrap, sys.modules["asyncio"].tasks._GatheringFuture.__init__)
        def _(f: typing.Callable[..., None], args: tuple[typing.Any, ...], kwargs: dict[str, typing.Any]) -> None:
            try:
                return f(*args, **kwargs)
            finally:
                children = get_argument_value(args, kwargs, 1, "children")
                assert children is not None  # nosec: assert is used for typing

                # Pass an invalid positional index for 'loop'
                loop = get_argument_value(args, kwargs, -1, "loop")

                # Link the parent gathering task to the gathered children
                parent = globals()["current_task"](loop)

                for child in children:
                    stack.link_tasks(parent, child)

        @partial(wrap, sys.modules["asyncio"].tasks._wait)
        def _(
            f: typing.Callable[
                ..., typing.Tuple[typing.Set["aio.Future[typing.Any]"], typing.Set["aio.Future[typing.Any]"]]
            ],
            args: tuple[typing.Any, ...],
            kwargs: dict[str, typing.Any],
        ) -> typing.Any:
            try:
                return f(*args, **kwargs)
            finally:
                futures = typing.cast(typing.Set["aio.Future[typing.Any]"], get_argument_value(args, kwargs, 0, "fs"))
                loop = typing.cast("aio.AbstractEventLoop", get_argument_value(args, kwargs, 3, "loop"))

                # Link the parent gathering task to the gathered children
                parent = typing.cast("aio.Task[typing.Any]", globals()["current_task"](loop))
                for future in futures:
                    stack.link_tasks(parent, future)

        @partial(wrap, sys.modules["asyncio"].tasks.as_completed)
        def _(
            f: typing.Callable[..., typing.Generator["aio.Future[typing.Any]", typing.Any, None]],
            args: tuple[typing.Any, ...],
            kwargs: dict[str, typing.Any],
        ) -> typing.Any:
            loop = typing.cast(typing.Optional["aio.AbstractEventLoop"], kwargs.get("loop"))
            parent: typing.Optional["aio.Task[typing.Any]"] = globals()["current_task"](loop)

            if parent is not None:
                fs = typing.cast(typing.Iterable["aio.Future[typing.Any]"], get_argument_value(args, kwargs, 0, "fs"))
                futures: typing.Set["aio.Future"] = {asyncio.ensure_future(f, loop=loop) for f in set(fs)}
                for future in futures:
                    stack.link_tasks(parent, future)

                # Replace fs with the ensured futures to avoid double-wrapping
                args = (futures,) + args[1:]

            return f(*args, **kwargs)

        _call_init_asyncio(asyncio)


def get_event_loop_for_thread(thread_id: int) -> typing.Optional["asyncio.AbstractEventLoop"]:
    global THREAD_LINK

    return THREAD_LINK.get_object(thread_id) if THREAD_LINK is not None else None
