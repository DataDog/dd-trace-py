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

from ddtrace.internal._unpatched import _threading as ddtrace_threading
from ddtrace.internal.compat import NEXT_PY_VERSION_INFO
from ddtrace.internal.datadog.profiling import stack
from ddtrace.internal.module import ModuleWatchdog
from ddtrace.internal.settings.profiling import config
from ddtrace.internal.utils import get_argument_value
from ddtrace.internal.wrapping import wrap


ASYNCIO_IMPORTED: bool = False
# wrap() raises from NEXT_PY_VERSION_INFO (3.15 on this stack). Keep bytecode
# wrapping on older CPythons; use sys.monitoring only where wrap() cannot run.
_USE_WRAP: bool = sys.version_info < NEXT_PY_VERSION_INFO


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


# On Python 3.15, wrapping is unavailable, so task-creation tracking uses
# sys.monitoring PY_RETURN (API since 3.12, PEP 669), which yields the new Task
# object directly. The version gate is wrapping-unavailability, not API novelty.
# Other asyncio hooks use attribute replacement on 3.15+ — CALL/PY_START
# callbacks don't expose the callee's arguments, so they have no advantage over
# monkey-patching. Below 3.15 every former wrap() site still uses wrap().
# TODO(py-315): Evaluate rolling this path out to 3.12–3.14 once #19601's
# multiplexer (3.12+) is the shared owner and CI covers those versions.
_monitoring_tool_id: typing.Optional[int] = None
# Maps id(code) -> handler(return_value) for PY_RETURN dispatch
_py_return_handlers: dict[int, typing.Callable[[object], None]] = {}


def _py_return_dispatch(code: CodeType, instruction_offset: int, return_value: object) -> None:
    handler: typing.Optional[typing.Callable[[object], None]] = _py_return_handlers.get(id(code))
    if handler is not None:
        handler(return_value)


def _register_return_hook(func: typing.Callable[..., typing.Any], handler: typing.Callable[[object], None]) -> bool:
    """Register a sys.monitoring PY_RETURN hook for *func*.

    Used on Python 3.15+ because wrapping is unavailable there, not because
    sys.monitoring is new (it exists since 3.12). Returns True if the hook was
    installed, False if the caller should fall back to wrap() (below 3.15) or
    monkey-patching (3.15+ when monitoring cannot be installed).
    """
    global _monitoring_tool_id

    if sys.version_info >= NEXT_PY_VERSION_INFO:
        m: typing.Any = sys.monitoring  # type: ignore[attr-defined]

        if _monitoring_tool_id is None:
            # Tool IDs 4-5 are free custom slots; 0-3 are reserved (debugger, coverage,
            # profiler, optimizer). Try from the top to minimise conflicts.
            candidate: int
            for candidate in (5, 4):
                try:
                    m.use_tool_id(candidate, "dd-profiling-asyncio")
                    m.register_callback(candidate, m.events.PY_RETURN, _py_return_dispatch)
                    _monitoring_tool_id = candidate
                    break
                except ValueError:
                    continue
            if _monitoring_tool_id is None:
                return False

        try:
            code: CodeType = func.__code__
            _py_return_handlers[id(code)] = handler
            m.set_local_events(_monitoring_tool_id, code, m.events.PY_RETURN)
            return True
        except Exception:
            return False  # nosec B110 — best-effort monitoring; fall back to wrap/patch

    return False


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
    events_module: ModuleType = sys.modules["asyncio.events"]
    if sys.hexversion >= 0x030E0000:
        policy_class: typing.Optional[type[typing.Any]] = getattr(events_module, "_BaseDefaultEventLoopPolicy", None)
    else:
        policy_class = getattr(events_module, "BaseDefaultEventLoopPolicy", None)

    if policy_class is not None:
        if _USE_WRAP:

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

        else:
            _original_sel: typing.Callable[..., None] = policy_class.set_event_loop

            def _patched_set_event_loop(self: typing.Any, loop: typing.Optional[aio.AbstractEventLoop]) -> None:
                if init_stack:
                    stack.track_asyncio_loop(typing.cast(int, ddtrace_threading.current_thread().ident), loop)
                _original_sel(self, loop)

            policy_class.set_event_loop = _patched_set_event_loop

    if init_stack:
        tasks_module: ModuleType = sys.modules["asyncio"].tasks

        if _USE_WRAP:

            @partial(wrap, tasks_module._GatheringFuture.__init__)
            def _(f: typing.Callable[..., None], args: tuple[typing.Any, ...], kwargs: dict[str, typing.Any]) -> None:
                try:
                    return f(*args, **kwargs)
                finally:
                    children: list[aio.Future[typing.Any]] = typing.cast(
                        "list[aio.Future[typing.Any]]", get_argument_value(args, kwargs, 1, "children")
                    )
                    assert children is not None  # nosec: assert is used for typing
                    parent: typing.Optional[aio.Task[typing.Any]]
                    try:
                        parent = globals()["current_task"]()
                    except RuntimeError:
                        parent = None
                    if parent is not None:
                        child: aio.Future[typing.Any]
                        for child in children:
                            stack.link_tasks(parent, child)

            @partial(wrap, tasks_module._wait)
            def _(
                f: typing.Callable[..., tuple[set[aio.Future[typing.Any]], set[aio.Future[typing.Any]]]],
                args: tuple[typing.Any, ...],
                kwargs: dict[str, typing.Any],
            ) -> typing.Any:
                try:
                    return f(*args, **kwargs)
                finally:
                    futures = typing.cast("set[aio.Future[typing.Any]]", get_argument_value(args, kwargs, 0, "fs"))
                    parent: typing.Optional[aio.Task[typing.Any]]
                    try:
                        parent = typing.cast("aio.Task[typing.Any]", globals()["current_task"]())
                    except RuntimeError:
                        parent = None
                    if parent is not None:
                        future: aio.Future[typing.Any]
                        for future in futures:
                            stack.link_tasks(parent, future)

            @partial(wrap, tasks_module.as_completed)
            def _(
                f: typing.Callable[..., typing.Generator[aio.Future[typing.Any], typing.Any, None]],
                args: tuple[typing.Any, ...],
                kwargs: dict[str, typing.Any],
            ) -> typing.Any:
                loop = typing.cast("typing.Optional[aio.AbstractEventLoop]", kwargs.get("loop"))
                parent: typing.Optional[aio.Task[typing.Any]] = globals()["current_task"]()

                if parent is not None:
                    fs = typing.cast(
                        "typing.Iterable[aio.Future[typing.Any]]", get_argument_value(args, kwargs, 0, "fs")
                    )
                    futures: set[aio.Future[typing.Any]] = {asyncio.ensure_future(f, loop=loop) for f in set(fs)}
                    future: aio.Future[typing.Any]
                    for future in futures:
                        stack.link_tasks(parent, future)

                    # Replace fs with the ensured futures to avoid double-wrapping.
                    if args:
                        args = (futures,) + args[1:]
                    else:
                        kwargs = {**kwargs, "fs": futures}

                return f(*args, **kwargs)

            @partial(wrap, tasks_module.shield)
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

                if args:
                    args = (future,) + args[1:]
                else:
                    kwargs = {**kwargs, "arg": future}

                return f(*args, **kwargs)

        else:
            # --- _GatheringFuture.__init__ ---
            _original_gf_init: typing.Callable[..., None] = tasks_module._GatheringFuture.__init__

            def _patched_gf_init(
                self: typing.Any,
                children: typing.Iterable[aio.Future[typing.Any]],
                *args: typing.Any,
                **kwargs: typing.Any,
            ) -> None:
                _original_gf_init(self, children, *args, **kwargs)
                try:
                    parent: typing.Optional[aio.Task[typing.Any]] = globals()["current_task"]()
                except RuntimeError:
                    return
                if parent is not None:
                    child: aio.Future[typing.Any]
                    for child in children:
                        stack.link_tasks(parent, child)

            tasks_module._GatheringFuture.__init__ = _patched_gf_init

            # --- asyncio.tasks._wait ---
            _original_wait: typing.Callable[..., typing.Any] = tasks_module._wait

            def _patched_wait(*args: typing.Any, **kwargs: typing.Any) -> typing.Any:
                fs: typing.Iterable[aio.Future[typing.Any]] = args[0] if args else kwargs.get("fs", ())
                try:
                    parent: typing.Optional[aio.Task[typing.Any]] = typing.cast(
                        "aio.Task[typing.Any]", globals()["current_task"]()
                    )
                except RuntimeError:
                    return _original_wait(*args, **kwargs)
                if parent is not None:
                    future: aio.Future[typing.Any]
                    for future in fs:
                        stack.link_tasks(parent, future)
                return _original_wait(*args, **kwargs)

            tasks_module._wait = _patched_wait  # type: ignore[attr-defined]

            # --- asyncio.tasks.as_completed ---
            _original_as_completed: typing.Callable[..., typing.Any] = tasks_module.as_completed

            def _patched_as_completed(*args: typing.Any, **kwargs: typing.Any) -> typing.Any:
                fs: typing.Iterable[aio.Future[typing.Any]] = args[0] if args else kwargs.get("fs", ())
                loop: typing.Optional[aio.AbstractEventLoop] = kwargs.get("loop")
                parent: typing.Optional[aio.Task[typing.Any]] = globals()["current_task"]()

                if parent is not None:
                    futures: set[aio.Future[typing.Any]] = {asyncio.ensure_future(f, loop=loop) for f in set(fs)}
                    future: aio.Future[typing.Any]
                    for future in futures:
                        stack.link_tasks(parent, future)
                    if args:
                        args = (futures,) + args[1:]
                    else:
                        kwargs = {**kwargs, "fs": futures}

                return _original_as_completed(*args, **kwargs)

            tasks_module.as_completed = _patched_as_completed  # type: ignore[attr-defined]
            asyncio.as_completed = _patched_as_completed  # type: ignore[attr-defined]

            # --- asyncio.tasks.shield ---
            _original_shield: typing.Callable[..., typing.Any] = tasks_module.shield

            def _patched_shield(*args: typing.Any, **kwargs: typing.Any) -> typing.Any:
                loop: typing.Optional[aio.AbstractEventLoop] = kwargs.get("loop")
                awaitable: aio.Future[typing.Any] = args[0] if args else kwargs["arg"]
                future: aio.Future[typing.Any] = asyncio.ensure_future(awaitable, loop=loop)

                parent: typing.Optional[aio.Task[typing.Any]] = globals()["current_task"]()
                if parent is not None:
                    stack.link_tasks(parent, future)

                if args:
                    args = (future,) + args[1:]
                else:
                    kwargs = {**kwargs, "arg": future}

                return _original_shield(*args, **kwargs)

            tasks_module.shield = _patched_shield  # type: ignore[attr-defined]
            asyncio.shield = _patched_shield  # type: ignore[attr-defined]

        # --- asyncio.TaskGroup.create_task (Python 3.11+) ---
        if sys.hexversion >= 0x030B0000:
            taskgroups_module: typing.Optional[ModuleType] = sys.modules.get("asyncio.taskgroups")
            if taskgroups_module is not None:
                taskgroup_class: typing.Optional[type[typing.Any]] = getattr(taskgroups_module, "TaskGroup", None)
                if taskgroup_class is not None and hasattr(taskgroup_class, "create_task"):
                    if _USE_WRAP:

                        @partial(wrap, taskgroup_class.create_task)
                        def _(
                            f: typing.Callable[..., aio.Task[typing.Any]],
                            args: tuple[typing.Any, ...],
                            kwargs: dict[str, typing.Any],
                        ) -> aio.Task[typing.Any]:
                            result: aio.Task[typing.Any] = f(*args, **kwargs)
                            try:
                                parent: typing.Optional[aio.Task[typing.Any]] = globals()["current_task"]()
                            except RuntimeError:
                                return result
                            if parent is not None and result is not None:
                                stack.link_tasks(parent, result)
                            return result

                    else:

                        def _on_taskgroup_create_task_return(return_value: object) -> None:
                            task: typing.Optional[aio.Task[typing.Any]] = typing.cast(
                                "typing.Optional[aio.Task[typing.Any]]", return_value
                            )
                            try:
                                parent: typing.Optional[aio.Task[typing.Any]] = globals()["current_task"]()
                            except RuntimeError:
                                return
                            if parent is not None and task is not None:
                                stack.link_tasks(parent, task)

                        if not _register_return_hook(taskgroup_class.create_task, _on_taskgroup_create_task_return):
                            _original_tg_create_task: typing.Callable[..., aio.Task[typing.Any]] = (
                                taskgroup_class.create_task
                            )

                            def _patched_tg_create_task(
                                self: typing.Any, *args: typing.Any, **kwargs: typing.Any
                            ) -> aio.Task[typing.Any]:
                                result: aio.Task[typing.Any] = _original_tg_create_task(self, *args, **kwargs)
                                try:
                                    parent: typing.Optional[aio.Task[typing.Any]] = globals()["current_task"]()
                                except RuntimeError:
                                    return result
                                if parent is not None and result is not None:
                                    stack.link_tasks(parent, result)
                                return result

                            taskgroup_class.create_task = _patched_tg_create_task

        # --- asyncio.tasks.create_task ---
        # Note: asyncio.timeout and asyncio.timeout_at don't create child tasks.
        # They are context managers that schedule a callback to cancel the current
        # task if it times out; the timeout._task IS the current task, so there's
        # no parent-child relationship to track.
        if _USE_WRAP:

            @partial(wrap, tasks_module.create_task)
            def _(
                f: typing.Callable[..., aio.Task[typing.Any]],
                args: tuple[typing.Any, ...],
                kwargs: dict[str, typing.Any],
            ) -> aio.Task[typing.Any]:
                task: aio.Task[typing.Any] = f(*args, **kwargs)
                try:
                    parent: typing.Optional[aio.Task[typing.Any]] = globals()["current_task"]()
                except RuntimeError:
                    return task
                if parent is not None:
                    stack.weak_link_tasks(parent, task)
                return task

        else:
            _original_create_task: typing.Callable[..., aio.Task[typing.Any]] = tasks_module.create_task

            def _on_create_task_return(return_value: object) -> None:
                task: aio.Task[typing.Any] = typing.cast("aio.Task[typing.Any]", return_value)
                try:
                    parent: typing.Optional[aio.Task[typing.Any]] = globals()["current_task"]()
                except RuntimeError:
                    return
                if parent is not None:
                    stack.weak_link_tasks(parent, task)

            if not _register_return_hook(_original_create_task, _on_create_task_return):

                def _patched_create_task(*args: typing.Any, **kwargs: typing.Any) -> "aio.Task[typing.Any]":
                    task: "aio.Task[typing.Any]" = _original_create_task(*args, **kwargs)
                    try:
                        parent: typing.Optional[aio.Task[typing.Any]] = globals()["current_task"]()
                    except RuntimeError:
                        return task
                    if parent is not None:
                        stack.weak_link_tasks(parent, task)
                    return task

                tasks_module.create_task = _patched_create_task  # type: ignore[attr-defined]
                asyncio.create_task = _patched_create_task  # type: ignore[attr-defined]

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
    if not config.stack.uvloop:  # pyright: ignore[reportAttributeAccessIssue]
        return

    import asyncio

    init_stack: bool = config.stack.enabled and stack.is_available

    new_event_loop_func: typing.Optional[typing.Callable[[], asyncio.AbstractEventLoop]] = getattr(
        uvloop, "new_event_loop", None
    )
    if new_event_loop_func is not None:
        if _USE_WRAP:

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
                    _call_init_asyncio(asyncio)
                return loop

        else:
            _original_nel: typing.Callable[[], asyncio.AbstractEventLoop] = new_event_loop_func

            def _patched_new_event_loop() -> asyncio.AbstractEventLoop:
                loop: asyncio.AbstractEventLoop = _original_nel()
                if init_stack:
                    thread_id: int = typing.cast(int, ddtrace_threading.current_thread().ident)
                    stack.set_uvloop_mode(thread_id, True)
                    stack.track_asyncio_loop(thread_id, loop)
                    _call_init_asyncio(asyncio)
                return loop

            uvloop.new_event_loop = _patched_new_event_loop  # type: ignore[attr-defined]

    policy_class: typing.Optional[type[typing.Any]] = getattr(uvloop, "EventLoopPolicy", None)
    if policy_class is not None and hasattr(policy_class, "set_event_loop"):
        if _USE_WRAP:

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
                    stack.track_asyncio_loop(thread_id, loop)
                    _call_init_asyncio(asyncio)
                return f(*args, **kwargs)

        else:
            _original_uvloop_sel: typing.Callable[..., None] = policy_class.set_event_loop

            def _patched_uvloop_set_event_loop(
                self: typing.Any, loop: typing.Optional[asyncio.AbstractEventLoop]
            ) -> None:
                thread_id: int = typing.cast(int, ddtrace_threading.current_thread().ident)
                if init_stack:
                    stack.set_uvloop_mode(thread_id, True)
                if init_stack and loop is not None:
                    stack.track_asyncio_loop(thread_id, loop)
                    _call_init_asyncio(asyncio)
                _original_uvloop_sel(self, loop)

            policy_class.set_event_loop = _patched_uvloop_set_event_loop
