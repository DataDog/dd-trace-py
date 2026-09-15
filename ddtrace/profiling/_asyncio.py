# -*- encoding: utf-8 -*-
from __future__ import annotations

from functools import partial
import sys
from types import CodeType
from types import ModuleType
import typing


if typing.TYPE_CHECKING:
    import asyncio
    import asyncio as aio

    from ddtrace.internal import monitoring as _monitoring

from ddtrace.internal._unpatched import _threading as ddtrace_threading
from ddtrace.internal.datadog.profiling import stack
from ddtrace.internal.module import ModuleWatchdog
from ddtrace.internal.settings.profiling import config
from ddtrace.internal.utils import get_argument_value
from ddtrace.internal.wrapping import wrap


if sys.version_info >= (3, 15):
    from ddtrace.internal import monitoring as _monitoring


ASYNCIO_IMPORTED: bool = False


_ASYNCIO_MONITORING_MIN: tuple[int, int] = (3, 15)
_monitoring_tool_id: typing.Optional[int] = None
_py_return_handlers: dict[int, typing.Callable[[object], None]] = {}


class _AsyncioReturnHookDispatch:
    """PY_RETURN dispatch for the asyncio task-creation hooks.

    Kept outside the version gate so it can be instantiated and exercised on
    interpreters below 3.15; _AsyncioMonitoringHandler adds the 3.15-only
    MonitoringEventHandler base.
    """

    def __init__(self) -> None:
        self.handlers: dict[int, typing.Callable[[object], None]] = _py_return_handlers

    def on_py_return(self, code: CodeType, instruction_offset: int, return_value: object) -> None:
        handler: typing.Optional[typing.Callable[[object], None]] = self.handlers.get(id(code))
        if handler is not None:
            handler(return_value)


if sys.version_info >= _ASYNCIO_MONITORING_MIN:

    class _AsyncioMonitoringHandler(_AsyncioReturnHookDispatch, _monitoring.MonitoringEventHandler):
        pass

    _monitoring_handler: typing.Optional[_AsyncioMonitoringHandler] = None


def _do_register_return_hook(
    monitoring_handler: _AsyncioReturnHookDispatch,
    func: typing.Callable[..., typing.Any],
    handler: typing.Callable[[object], None],
) -> bool:
    """Point monitoring_handler at handler for the code object of func.

    Split out of _register_return_hook, which owns the version gate, so the
    registration and unwinding paths stay testable below 3.15.
    """
    global _monitoring_tool_id

    event_handler: typing.Any = typing.cast(typing.Any, monitoring_handler)
    code: typing.Optional[CodeType] = None
    try:
        code = func.__code__
        monitoring_handler.handlers[id(code)] = handler
        _monitoring.register(code, event_handler)
        _monitoring_tool_id = _monitoring.get_tool_id()
        return True
    except Exception:
        if code is not None:
            monitoring_handler.handlers.pop(id(code), None)
            try:
                _monitoring.unregister(code, event_handler)
            except Exception:  # nosec B110 — unwinding an already-failed registration
                pass
        return False  # best-effort monitoring; fall back to wrap()


def _register_return_hook(func: typing.Callable[..., typing.Any], handler: typing.Callable[[object], None]) -> bool:
    if sys.version_info >= _ASYNCIO_MONITORING_MIN:
        global _monitoring_handler

        if _monitoring_handler is None:
            _monitoring_handler = _AsyncioMonitoringHandler()

        return _do_register_return_hook(_monitoring_handler, func, handler)

    return False


def current_task() -> typing.Optional[asyncio.Task[typing.Any]]:
    return None


def get_running_loop() -> typing.Optional[asyncio.AbstractEventLoop]:
    return None


def _task_get_name(task: asyncio.Task[typing.Any]) -> str:
    return "Task-%d" % id(task)


def _call_init_asyncio(asyncio: ModuleType) -> None:
    from asyncio import tasks as asyncio_tasks

    if sys.hexversion >= 0x030C0000:
        scheduled_tasks = asyncio_tasks._scheduled_tasks.data  # type: ignore[attr-defined]
        eager_tasks = asyncio_tasks._eager_tasks  # type: ignore[attr-defined]
    else:
        scheduled_tasks = asyncio_tasks._all_tasks.data  # type: ignore[attr-defined]
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
    running_loop: typing.Optional[asyncio.AbstractEventLoop] = None
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

    def _get_running_loop() -> typing.Optional[aio.AbstractEventLoop]:
        try:
            return typing.cast("aio.AbstractEventLoop", asyncio.get_running_loop())
        except RuntimeError:
            return None

    globals()["get_running_loop"] = _get_running_loop
    globals()["_task_get_name"] = lambda task: task.get_name()

    init_stack: bool = config.stack.enabled and stack.is_available

    # Python 3.14+: BaseDefaultEventLoopPolicy was renamed to _BaseDefaultEventLoopPolicy
    # Try both names for compatibility
    events_module: ModuleType = sys.modules["asyncio.events"]
    if sys.hexversion >= 0x030E0000:
        # Python 3.14+: Use _BaseDefaultEventLoopPolicy
        policy_class: typing.Optional[type[typing.Any]] = getattr(events_module, "_BaseDefaultEventLoopPolicy", None)
    else:
        # Python < 3.14: Use BaseDefaultEventLoopPolicy
        policy_class = getattr(events_module, "BaseDefaultEventLoopPolicy", None)

    if policy_class is not None:

        @partial(wrap, policy_class.set_event_loop)  # pyright: ignore[reportArgumentType]
        def _(
            f: typing.Callable[[object, typing.Optional[aio.AbstractEventLoop]], None],
            args: typing.Any,
            kwargs: typing.Any,
        ) -> None:
            loop: typing.Optional[aio.AbstractEventLoop] = get_argument_value(args, kwargs, 1, "loop")
            if init_stack:
                stack.track_asyncio_loop(typing.cast(int, ddtrace_threading.current_thread().ident), loop)
            return f(*args, **kwargs)

    if init_stack:

        @partial(wrap, sys.modules["asyncio"].tasks._GatheringFuture.__init__)
        def _(f: typing.Callable[..., None], args: tuple[typing.Any, ...], kwargs: dict[str, typing.Any]) -> None:
            try:
                return f(*args, **kwargs)
            finally:
                children: list[aio.Future[typing.Any]] = typing.cast(
                    "list[aio.Future[typing.Any]]", get_argument_value(args, kwargs, 1, "children")
                )
                assert children is not None  # nosec: assert is used for typing

                if globals()["get_running_loop"]() is not None:
                    parent: typing.Optional[aio.Task[typing.Any]] = globals()["current_task"]()
                    if parent is not None:
                        for child in children:
                            stack.link_tasks(parent, child)

        @partial(wrap, sys.modules["asyncio"].tasks._wait)
        def _(
            f: typing.Callable[..., tuple[set[aio.Future[typing.Any]], set[aio.Future[typing.Any]]]],
            args: tuple[typing.Any, ...],
            kwargs: dict[str, typing.Any],
        ) -> typing.Any:
            try:
                return f(*args, **kwargs)
            finally:
                futures = typing.cast("set[aio.Future[typing.Any]]", get_argument_value(args, kwargs, 0, "fs"))

                if globals()["get_running_loop"]() is not None:
                    parent = typing.cast("aio.Task[typing.Any]", globals()["current_task"]())
                    for future in futures:
                        stack.link_tasks(parent, future)

        @partial(wrap, sys.modules["asyncio"].tasks.as_completed)
        def _(
            f: typing.Callable[..., typing.Generator[aio.Future[typing.Any], typing.Any, None]],
            args: tuple[typing.Any, ...],
            kwargs: dict[str, typing.Any],
        ) -> typing.Any:
            loop = typing.cast("typing.Optional[aio.AbstractEventLoop]", kwargs.get("loop"))
            parent: typing.Optional[aio.Task[typing.Any]] = globals()["current_task"]()

            if parent is not None:
                fs = typing.cast("typing.Iterable[aio.Future[typing.Any]]", get_argument_value(args, kwargs, 0, "fs"))
                futures: set[aio.Future[typing.Any]] = {asyncio.ensure_future(f, loop=loop) for f in set(fs)}
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
            f: typing.Callable[..., aio.Future[typing.Any]],
            args: tuple[typing.Any, ...],
            kwargs: dict[str, typing.Any],
        ) -> typing.Any:
            loop = typing.cast("typing.Optional[aio.AbstractEventLoop]", kwargs.get("loop"))
            awaitable = typing.cast("aio.Future[typing.Any]", get_argument_value(args, kwargs, 0, "arg"))
            future: aio.Future[typing.Any] = asyncio.ensure_future(awaitable, loop=loop)

            parent: typing.Optional[aio.Task[typing.Any]] = globals()["current_task"]()
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

        # Hook asyncio.TaskGroup.create_task to link parent task to created tasks (Python 3.11+).
        if sys.hexversion >= 0x030B0000:
            taskgroups_module: typing.Optional[ModuleType] = sys.modules.get("asyncio.taskgroups")
            if taskgroups_module is not None:
                taskgroup_class: typing.Optional[type[typing.Any]] = getattr(taskgroups_module, "TaskGroup", None)
                if taskgroup_class is not None and hasattr(taskgroup_class, "create_task"):

                    def _on_taskgroup_create_task_return(return_value: object) -> None:
                        task: typing.Optional[aio.Task[typing.Any]] = typing.cast(
                            "typing.Optional[aio.Task[typing.Any]]", return_value
                        )
                        parent: typing.Optional[aio.Task[typing.Any]] = globals()["current_task"]()
                        if parent is not None and task is not None:
                            stack.link_tasks(parent, task)

                    if not _register_return_hook(taskgroup_class.create_task, _on_taskgroup_create_task_return):

                        @partial(wrap, taskgroup_class.create_task)
                        def _(
                            f: typing.Callable[..., aio.Task[typing.Any]],
                            args: tuple[typing.Any, ...],
                            kwargs: dict[str, typing.Any],
                        ) -> aio.Task[typing.Any]:
                            result: aio.Task[typing.Any] = f(*args, **kwargs)
                            parent: typing.Optional[aio.Task[typing.Any]] = globals()["current_task"]()
                            if parent is not None and result is not None:
                                stack.link_tasks(parent, result)
                            return result

        # Note: asyncio.timeout and asyncio.timeout_at don't create child tasks.
        # They are context managers that schedule a callback to cancel the current task
        # if it times out. The timeout._task is the same as the current task, so there's
        # no parent-child relationship to link. The timeout mechanism is handled by the
        # event loop's timeout handler, not by creating new tasks.
        def _on_create_task_return(return_value: object) -> None:
            task: aio.Task[typing.Any] = typing.cast("aio.Task[typing.Any]", return_value)
            parent: typing.Optional[aio.Task[typing.Any]] = globals()["current_task"]()
            if parent is not None:
                stack.weak_link_tasks(parent, task)

        if not _register_return_hook(sys.modules["asyncio"].tasks.create_task, _on_create_task_return):

            @partial(wrap, sys.modules["asyncio"].tasks.create_task)
            def _(
                f: typing.Callable[..., aio.Task[typing.Any]],
                args: tuple[typing.Any, ...],
                kwargs: dict[str, typing.Any],
            ) -> aio.Task[typing.Any]:
                task: aio.Task[typing.Any] = f(*args, **kwargs)
                parent: typing.Optional[aio.Task[typing.Any]] = globals()["current_task"]()
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
    new_event_loop_func: typing.Optional[typing.Callable[[], asyncio.AbstractEventLoop]] = getattr(
        uvloop, "new_event_loop", None
    )
    if new_event_loop_func is not None:

        @partial(wrap, new_event_loop_func)  # type: ignore[arg-type]
        def _(
            f: typing.Callable[[], asyncio.AbstractEventLoop],
            args: tuple[typing.Any, ...],
            kwargs: dict[str, typing.Any],
        ) -> asyncio.AbstractEventLoop:
            loop: asyncio.AbstractEventLoop = f(*args, **kwargs)
            if init_stack:
                thread_id: int = typing.cast(int, ddtrace_threading.current_thread().ident)
                stack.set_uvloop_mode(thread_id, True)

                stack.track_asyncio_loop(thread_id, loop)
                # Ensure asyncio task tracking is initialized
                _call_init_asyncio(asyncio)

            return loop

    # Wrap uvloop.EventLoopPolicy.set_event_loop for uvloop.install() + asyncio.run() pattern
    policy_class: typing.Optional[type[typing.Any]] = getattr(uvloop, "EventLoopPolicy", None)
    if policy_class is not None and hasattr(policy_class, "set_event_loop"):

        @partial(wrap, policy_class.set_event_loop)  # pyright: ignore[reportArgumentType]
        def _(
            f: typing.Callable[[object, typing.Optional[asyncio.AbstractEventLoop]], None],
            args: typing.Any,
            kwargs: typing.Any,
        ) -> None:
            thread_id: int = typing.cast(int, ddtrace_threading.current_thread().ident)
            if init_stack:
                stack.set_uvloop_mode(thread_id, True)

            loop: typing.Optional[asyncio.AbstractEventLoop] = get_argument_value(args, kwargs, 1, "loop")
            if init_stack and loop is not None:
                stack.track_asyncio_loop(typing.cast(int, ddtrace_threading.current_thread().ident), loop)
                _call_init_asyncio(asyncio)

            return f(*args, **kwargs)
