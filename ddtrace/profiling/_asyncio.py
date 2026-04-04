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
from ddtrace.internal.logger import get_logger
from ddtrace.internal.module import ModuleWatchdog
from ddtrace.internal.settings.profiling import config
from ddtrace.internal.utils import get_argument_value
from ddtrace.internal.wrapping import wrap


log = get_logger(__name__)


ASYNCIO_IMPORTED: bool = False


def current_task() -> typing.Optional["asyncio.Task[typing.Any]"]:
    return None


def get_running_loop() -> typing.Optional["asyncio.AbstractEventLoop"]:
    return None


def _task_get_name(task: "asyncio.Task[typing.Any]") -> str:
    return "Task-%d" % id(task)


def _call_init_asyncio(asyncio: ModuleType) -> None:
    from asyncio import tasks as asyncio_tasks

    # AIDEV-NOTE: _scheduled_tasks, _eager_tasks, and _all_tasks are asyncio internals.
    # They are confirmed present through Python 3.15. On known versions (<=3.15) a missing
    # attribute is a real bug; on unknown future versions (>=3.16) degrade gracefully.
    _on_known_version = sys.hexversion < 0x03100000  # < 3.16

    if sys.hexversion >= 0x030C0000:
        if not hasattr(asyncio_tasks, "_scheduled_tasks"):
            if _on_known_version:
                raise RuntimeError(
                    "ddtrace profiler: asyncio.tasks._scheduled_tasks not found on "
                    "Python %s. asyncio task tracking will not work. "
                    "Please report this at https://github.com/DataDog/dd-trace-py/issues" % sys.version
                )
            else:
                log.warning(
                    "ddtrace profiler: asyncio.tasks._scheduled_tasks not found on "
                    "Python %s. asyncio task tracking will be incomplete.",
                    sys.version,
                )
                return
        scheduled_tasks = getattr(asyncio_tasks, "_scheduled_tasks").data
        eager_tasks = getattr(asyncio_tasks, "_eager_tasks", None)
    else:
        if not hasattr(asyncio_tasks, "_all_tasks"):
            if _on_known_version:
                raise RuntimeError(
                    "ddtrace profiler: asyncio.tasks._all_tasks not found on "
                    "Python %s. asyncio task tracking will not work. "
                    "Please report this at https://github.com/DataDog/dd-trace-py/issues" % sys.version
                )
            else:
                log.warning(
                    "ddtrace profiler: asyncio.tasks._all_tasks not found on "
                    "Python %s. asyncio task tracking will be incomplete.",
                    sys.version,
                )
                return
        scheduled_tasks = getattr(asyncio_tasks, "_all_tasks").data
        eager_tasks = None

    stack.init_asyncio(scheduled_tasks, eager_tasks)


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
    stack.track_asyncio_loop(typing.cast(int, ddtrace_threading.current_thread().ident), running_loop)
    _call_init_asyncio(asyncio)


@ModuleWatchdog.after_module_imported("asyncio")
def _(asyncio: ModuleType) -> None:
    global ASYNCIO_IMPORTED

    ASYNCIO_IMPORTED = True

    if hasattr(asyncio, "current_task"):
        globals()["current_task"] = asyncio.current_task
    elif hasattr(asyncio.Task, "current_task"):
        globals()["current_task"] = asyncio.Task.current_task

    def _get_running_loop() -> typing.Optional["aio.AbstractEventLoop"]:
        try:
            return typing.cast("aio.AbstractEventLoop", asyncio.get_running_loop())
        except RuntimeError:
            return None

    globals()["get_running_loop"] = _get_running_loop
    globals()["_task_get_name"] = lambda task: task.get_name()

    init_stack: bool = config.stack.enabled and stack.is_available

    # Python 3.14+: BaseDefaultEventLoopPolicy was renamed to _BaseDefaultEventLoopPolicy.
    # AIDEV-TODO: The entire asyncio policy system is deprecated in 3.15 (CPython #127949)
    # and scheduled for removal in 3.16. When porting to 3.16, this block needs to be
    # replaced — the policy hook won't exist. Use asyncio.run(loop_factory=...) instead.
    # See docs/contributing-profiling-new-cpython.rst for migration guidance.
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
            if init_stack:
                stack.track_asyncio_loop(typing.cast(int, ddtrace_threading.current_thread().ident), loop)
            return f(*args, **kwargs)

    if init_stack:
        # AIDEV-NOTE: _GatheringFuture and _wait are asyncio internals with no stability
        # guarantee. On Python <= 3.15 (versions we've explicitly validated), we raise
        # immediately if they're missing — that's a real bug, not a graceful degradation.
        # On Python >= 3.16 (where the asyncio policy system is being removed and these
        # may go too), we degrade gracefully with a warning instead of crashing.
        # AIDEV-TODO: asyncio policy system deprecated in 3.15 (CPython #127949), removed
        # in 3.16. When porting to 3.16, revisit this whole block — _GatheringFuture and
        # _wait may also be gone. Replace with asyncio.run(loop_factory=...) pattern.
        # See docs/contributing-profiling-new-cpython.rst.
        _on_known_version = sys.hexversion < 0x03100000  # < 3.16

        if hasattr(sys.modules["asyncio"].tasks, "_GatheringFuture"):

            @partial(wrap, sys.modules["asyncio"].tasks._GatheringFuture.__init__)
            def _wrap_gathering_future(
                f: typing.Callable[..., None], args: tuple[typing.Any, ...], kwargs: dict[str, typing.Any]
            ) -> None:
                try:
                    return f(*args, **kwargs)
                finally:
                    children = get_argument_value(args, kwargs, 1, "children")
                    assert children is not None  # nosec: assert is used for typing

                    parent = globals()["current_task"]()
                    for child in children:
                        stack.link_tasks(parent, child)

        elif _on_known_version:
            raise RuntimeError(
                "ddtrace profiler: asyncio.tasks._GatheringFuture not found on Python %s. "
                "asyncio.gather() task-parent linking will not work. "
                "Please report this at https://github.com/DataDog/dd-trace-py/issues" % sys.version
            )
        else:
            log.warning(
                "ddtrace profiler: asyncio.tasks._GatheringFuture not found on Python %s. "
                "asyncio.gather() task-parent links will be missing from profiler data. "
                "This may be caused by asyncio internals changing in this Python version.",
                sys.version,
            )

        if hasattr(sys.modules["asyncio"].tasks, "_wait"):

            @partial(wrap, sys.modules["asyncio"].tasks._wait)
            def _wrap_wait(
                f: typing.Callable[..., tuple[set["aio.Future[typing.Any]"], set["aio.Future[typing.Any]"]]],
                args: tuple[typing.Any, ...],
                kwargs: dict[str, typing.Any],
            ) -> typing.Any:
                try:
                    return f(*args, **kwargs)
                finally:
                    futures = typing.cast(set["aio.Future[typing.Any]"], get_argument_value(args, kwargs, 0, "fs"))

                    parent = typing.cast("aio.Task[typing.Any]", globals()["current_task"]())
                    for future in futures:
                        stack.link_tasks(parent, future)

        elif _on_known_version:
            raise RuntimeError(
                f"ddtrace profiler: asyncio.tasks._wait not found on Python {sys.version}. "
                "asyncio.wait() task-parent linking will not work. "
                "Please report this at https://github.com/DataDog/dd-trace-py/issues."
            )
        else:
            log.warning(
                "ddtrace profiler: asyncio.tasks._wait not found on Python %s. "
                "asyncio.wait() task-parent links will be missing from profiler data. "
                "This may be caused by asyncio internals changing in this Python version.",
                sys.version,
            )

        @partial(wrap, sys.modules["asyncio"].tasks.as_completed)
        def _(
            f: typing.Callable[..., typing.Generator["aio.Future[typing.Any]", typing.Any, None]],
            args: tuple[typing.Any, ...],
            kwargs: dict[str, typing.Any],
        ) -> typing.Any:
            loop = typing.cast(typing.Optional["aio.AbstractEventLoop"], kwargs.get("loop"))
            parent: typing.Optional["aio.Task[typing.Any]"] = globals()["current_task"]()

            if parent is not None:
                fs = typing.cast(typing.Iterable["aio.Future[typing.Any]"], get_argument_value(args, kwargs, 0, "fs"))
                futures: set["aio.Future[typing.Any]"] = {asyncio.ensure_future(f, loop=loop) for f in set(fs)}
                for future in futures:
                    stack.link_tasks(parent, future)

                # Replace fs with the ensured futures to avoid double-wrapping.
                # Handle both positional (args[0]) and keyword ('fs') call patterns:
                # if fs was positional we update args; if it was a keyword we must
                # update kwargs instead, otherwise f() receives fs twice and raises
                # TypeError: got multiple values for argument 'fs'.
                if args:
                    args = (futures,) + args[1:]
                else:
                    kwargs = {**kwargs, "fs": futures}

            return f(*args, **kwargs)

        # Wrap asyncio.shield to link parent task to shielded future
        @partial(wrap, sys.modules["asyncio"].tasks.shield)
        def _(
            f: typing.Callable[..., "aio.Future[typing.Any]"],
            args: tuple[typing.Any, ...],
            kwargs: dict[str, typing.Any],
        ) -> typing.Any:
            loop = typing.cast(typing.Optional["aio.AbstractEventLoop"], kwargs.get("loop"))
            awaitable = typing.cast("aio.Future[typing.Any]", get_argument_value(args, kwargs, 0, "arg"))
            future = asyncio.ensure_future(awaitable, loop=loop)

            parent = globals()["current_task"]()
            if parent is not None:
                stack.link_tasks(parent, future)

            # Same positional-vs-keyword handling as the as_completed wrapper above:
            # if 'arg' was passed positionally update args, otherwise update kwargs to
            # avoid TypeError: got multiple values for argument 'arg'.
            if args:
                args = (future,) + args[1:]
            else:
                kwargs = {**kwargs, "arg": future}

            return f(*args, **kwargs)

        # Wrap asyncio.TaskGroup.create_task to link parent task to created tasks (Python 3.11+)
        if sys.hexversion >= 0x030B0000:  # Python 3.11+
            taskgroups_module = sys.modules.get("asyncio.taskgroups")
            if taskgroups_module is not None:
                taskgroup_class = getattr(taskgroups_module, "TaskGroup", None)
                if taskgroup_class is not None and hasattr(taskgroup_class, "create_task"):

                    @partial(wrap, taskgroup_class.create_task)
                    def _(
                        f: typing.Callable[..., "aio.Task[typing.Any]"],
                        args: tuple[typing.Any, ...],
                        kwargs: dict[str, typing.Any],
                    ) -> typing.Any:
                        result = f(*args, **kwargs)

                        parent = globals()["current_task"]()
                        if parent is not None and result is not None:
                            # Link parent task to the task created by TaskGroup
                            stack.link_tasks(parent, result)

                        return result

        # Note: asyncio.timeout and asyncio.timeout_at don't create child tasks.
        # They are context managers that schedule a callback to cancel the current task
        # if it times out. The timeout._task is the same as the current task, so there's
        # no parent-child relationship to link. The timeout mechanism is handled by the
        # event loop's timeout handler, not by creating new tasks.
        @partial(wrap, sys.modules["asyncio"].tasks.create_task)
        def _(
            f: typing.Callable[..., "aio.Task[typing.Any]"],
            args: tuple[typing.Any, ...],
            kwargs: dict[str, typing.Any],
        ) -> "aio.Task[typing.Any]":
            # kwargs will typically contain context (Python 3.11+ only) and eager_start (Python 3.14+ only)
            task: "aio.Task[typing.Any]" = f(*args, **kwargs)
            parent: typing.Optional["aio.Task[typing.Any]"] = globals()["current_task"]()

            if parent is not None:
                stack.weak_link_tasks(parent, task)

            return task

        _call_init_asyncio(asyncio)


@ModuleWatchdog.after_module_imported("uvloop")
def _(uvloop: ModuleType) -> None:
    """Hook uvloop to track event loops.

    uvloop doesn't inherit from BaseDefaultEventLoopPolicy, and on Python 3.11+
    uvloop.run() uses asyncio.Runner which bypasses set_event_loop entirely.
    We hook new_event_loop to catch all uvloop loop creations.

    We also hook EventLoopPolicy.set_event_loop for the deprecated uvloop.install()
    + asyncio.run() pattern.
    """
    # Check if uvloop support is disabled via configuration
    if not config.stack.uvloop:  # pyright: ignore[reportAttributeAccessIssue]
        return

    import asyncio

    init_stack: bool = config.stack.enabled and stack.is_available

    # Wrap uvloop.new_event_loop to track loops when they're created
    new_event_loop_func = getattr(uvloop, "new_event_loop", None)
    if new_event_loop_func is not None:

        @partial(wrap, new_event_loop_func)
        def _(
            f: typing.Callable[..., "asyncio.AbstractEventLoop"],
            args: tuple[typing.Any, ...],
            kwargs: dict[str, typing.Any],
        ) -> "asyncio.AbstractEventLoop":
            loop = f(*args, **kwargs)
            if init_stack:
                thread_id = typing.cast(int, ddtrace_threading.current_thread().ident)
                stack.set_uvloop_mode(thread_id, True)

                stack.track_asyncio_loop(thread_id, loop)
                # Ensure asyncio task tracking is initialized
                _call_init_asyncio(asyncio)

            return loop

    # Wrap uvloop.EventLoopPolicy.set_event_loop for uvloop.install() + asyncio.run() pattern
    policy_class = getattr(uvloop, "EventLoopPolicy", None)
    if policy_class is not None and hasattr(policy_class, "set_event_loop"):

        @partial(wrap, policy_class.set_event_loop)
        def _(
            f: typing.Callable[..., typing.Any], args: tuple[typing.Any, ...], kwargs: dict[str, typing.Any]
        ) -> typing.Any:
            thread_id = typing.cast(int, ddtrace_threading.current_thread().ident)
            if init_stack:
                stack.set_uvloop_mode(thread_id, True)

            loop: typing.Optional["asyncio.AbstractEventLoop"] = get_argument_value(args, kwargs, 1, "loop")
            if init_stack and loop is not None:
                stack.track_asyncio_loop(typing.cast(int, ddtrace_threading.current_thread().ident), loop)
                _call_init_asyncio(asyncio)

            return f(*args, **kwargs)
