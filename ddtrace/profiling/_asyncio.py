# -*- encoding: utf-8 -*-
from __future__ import annotations

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


ASYNCIO_IMPORTED: bool = False


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


# TODO(py-315): sys.monitoring (Python 3.15+) is used for task-creation tracking
# on create_task / TaskGroup.create_task, where PY_RETURN gives us the new Task
# object directly. All other asyncio hooks use simple attribute replacement —
# sys.monitoring CALL/PY_START callbacks don't expose the callee's arguments, so
# there is no advantage over plain monkey-patching for those sites.
# TODO(py-315): Evaluate rolling sys.monitoring path out to 3.12–3.14 as a Q3
# follow-up once there's proper CI coverage for those versions.
_monitoring_tool_id: typing.Optional[int] = None
# Maps id(code) -> handler(return_value) for PY_RETURN dispatch
_py_return_handlers: dict[int, typing.Callable[[typing.Any], None]] = {}


def _py_return_dispatch(code: typing.Any, instruction_offset: int, return_value: typing.Any) -> None:
    handler = _py_return_handlers.get(id(code))
    if handler is not None:
        handler(return_value)


def _register_return_hook(func: typing.Callable[..., typing.Any], handler: typing.Callable[[typing.Any], None]) -> bool:
    """Register a sys.monitoring PY_RETURN hook for *func* on Python 3.15+.

    Returns True if the hook was installed, False if sys.monitoring is not used
    (Python < 3.15) and the caller should fall back to monkey-patching.
    """
    global _monitoring_tool_id

    if sys.version_info >= (3, 15):
        m = sys.monitoring  # type: ignore[attr-defined]

        if _monitoring_tool_id is None:
            # Tool IDs 4-5 are free custom slots; 0-3 are reserved (debugger, coverage,
            # profiler, optimizer). Try from the top to minimise conflicts.
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
            code = func.__code__
            _py_return_handlers[id(code)] = handler
            m.set_local_events(_monitoring_tool_id, code, m.events.PY_RETURN)
            return True
        except Exception:
            return False  # nosec B110 — best-effort monitoring; fall back to monkey-patch

    return False


def _unregister_all_return_hooks() -> None:
    """Remove all PY_RETURN monitoring hooks (called on profiler stop)."""
    global _monitoring_tool_id

    _py_return_handlers.clear()

    if _monitoring_tool_id is not None and sys.version_info >= (3, 15):
        try:
            sys.monitoring.free_tool_id(_monitoring_tool_id)  # type: ignore[attr-defined]
        except Exception:  # nosec B110 — best-effort cleanup
            pass
        _monitoring_tool_id = None


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
        _original_sel = policy_class.set_event_loop

        def _patched_set_event_loop(self: typing.Any, loop: typing.Optional[aio.AbstractEventLoop]) -> None:
            if init_stack:
                stack.track_asyncio_loop(typing.cast(int, ddtrace_threading.current_thread().ident), loop)
            _original_sel(self, loop)

        policy_class.set_event_loop = _patched_set_event_loop

    if init_stack:
        tasks_module: ModuleType = sys.modules["asyncio"].tasks

        # --- _GatheringFuture.__init__ ---
        _original_gf_init = tasks_module._GatheringFuture.__init__

        def _patched_gf_init(
            self: typing.Any,
            children: typing.Iterable[aio.Future[typing.Any]],
            *args: typing.Any,
            **kwargs: typing.Any,
        ) -> None:
            _original_gf_init(self, children, *args, **kwargs)
            # TODO(py-315): current_task() raises RuntimeError on Python 3.15+ when there
            # is no running event loop (e.g. asyncio.gather() called outside an async
            # context to build a coroutine for later scheduling). In that case there is
            # no parent task to link from, so we skip link_tasks entirely.
            try:
                parent = globals()["current_task"]()
            except RuntimeError:
                return
            if parent is not None:
                for child in children:
                    stack.link_tasks(parent, child)

        tasks_module._GatheringFuture.__init__ = _patched_gf_init

        # --- asyncio.tasks._wait ---
        _original_wait = tasks_module._wait

        def _patched_wait(*args: typing.Any, **kwargs: typing.Any) -> typing.Any:
            # fs is the first positional or the 'fs' keyword argument
            fs: typing.Iterable[aio.Future[typing.Any]] = args[0] if args else kwargs.get("fs", ())
            # TODO(py-315): same guard as the _GatheringFuture wrapper above — _wait may
            # also be invoked outside a running loop. Skip link_tasks when current_task()
            # raises.
            try:
                parent = typing.cast("aio.Task[typing.Any]", globals()["current_task"]())
            except RuntimeError:
                return _original_wait(*args, **kwargs)
            if parent is not None:
                for future in fs:
                    stack.link_tasks(parent, future)
            return _original_wait(*args, **kwargs)

        tasks_module._wait = _patched_wait  # type: ignore[attr-defined]

        # --- asyncio.tasks.as_completed ---
        _original_as_completed = tasks_module.as_completed

        def _patched_as_completed(*args: typing.Any, **kwargs: typing.Any) -> typing.Any:
            fs: typing.Iterable[aio.Future[typing.Any]] = args[0] if args else kwargs.get("fs", ())
            loop: typing.Optional[aio.AbstractEventLoop] = kwargs.get("loop")
            parent: typing.Optional[aio.Task[typing.Any]] = globals()["current_task"]()

            if parent is not None:
                futures: set[aio.Future[typing.Any]] = {asyncio.ensure_future(f, loop=loop) for f in set(fs)}
                for future in futures:
                    stack.link_tasks(parent, future)
                # Replace fs with the ensured futures to avoid double-wrapping.
                if args:
                    args = (futures,) + args[1:]
                else:
                    kwargs = {**kwargs, "fs": futures}

            return _original_as_completed(*args, **kwargs)

        tasks_module.as_completed = _patched_as_completed  # type: ignore[attr-defined]
        asyncio.as_completed = _patched_as_completed  # type: ignore[attr-defined]  # re-export alias

        # --- asyncio.tasks.shield ---
        _original_shield = tasks_module.shield

        def _patched_shield(*args: typing.Any, **kwargs: typing.Any) -> typing.Any:
            loop: typing.Optional[aio.AbstractEventLoop] = kwargs.get("loop")
            awaitable: aio.Future[typing.Any] = args[0] if args else kwargs["arg"]
            future: aio.Future[typing.Any] = asyncio.ensure_future(awaitable, loop=loop)

            parent: typing.Optional[aio.Task[typing.Any]] = globals()["current_task"]()
            if parent is not None:
                stack.link_tasks(parent, future)

            # Replace the awaitable argument with the ensured future.
            if args:
                args = (future,) + args[1:]
            else:
                kwargs = {**kwargs, "arg": future}

            return _original_shield(*args, **kwargs)

        tasks_module.shield = _patched_shield  # type: ignore[attr-defined]
        asyncio.shield = _patched_shield  # type: ignore[attr-defined]  # re-export alias

        # --- asyncio.TaskGroup.create_task (Python 3.11+) ---
        if sys.hexversion >= 0x030B0000:
            taskgroups_module: typing.Optional[ModuleType] = sys.modules.get("asyncio.taskgroups")
            if taskgroups_module is not None:
                taskgroup_class: typing.Optional[type[typing.Any]] = getattr(taskgroups_module, "TaskGroup", None)
                if taskgroup_class is not None and hasattr(taskgroup_class, "create_task"):

                    def _on_taskgroup_create_task_return(return_value: typing.Any) -> None:
                        task: typing.Optional[aio.Task[typing.Any]] = return_value
                        try:
                            parent = globals()["current_task"]()
                        except RuntimeError:
                            return
                        if parent is not None and task is not None:
                            stack.link_tasks(parent, task)

                    if not _register_return_hook(taskgroup_class.create_task, _on_taskgroup_create_task_return):
                        # Fallback for Python < 3.15: simple monkey-patch
                        _original_tg_create_task = taskgroup_class.create_task

                        def _patched_tg_create_task(
                            self: typing.Any, *args: typing.Any, **kwargs: typing.Any
                        ) -> aio.Task[typing.Any]:
                            result: aio.Task[typing.Any] = _original_tg_create_task(self, *args, **kwargs)
                            try:
                                parent = globals()["current_task"]()
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
        _original_create_task = tasks_module.create_task

        def _on_create_task_return(return_value: typing.Any) -> None:
            task: aio.Task[typing.Any] = return_value
            try:
                parent = globals()["current_task"]()
            except RuntimeError:
                return
            if parent is not None:
                stack.weak_link_tasks(parent, task)

        if not _register_return_hook(_original_create_task, _on_create_task_return):
            # Fallback for Python < 3.15: simple monkey-patch
            def _patched_create_task(*args: typing.Any, **kwargs: typing.Any) -> "aio.Task[typing.Any]":
                task: "aio.Task[typing.Any]" = _original_create_task(*args, **kwargs)
                try:
                    parent = globals()["current_task"]()
                except RuntimeError:
                    return task
                if parent is not None:
                    stack.weak_link_tasks(parent, task)
                return task

            tasks_module.create_task = _patched_create_task  # type: ignore[attr-defined]
            asyncio.create_task = _patched_create_task  # type: ignore[attr-defined]  # re-export alias

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
        _original_nel = new_event_loop_func

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
        _original_uvloop_sel = policy_class.set_event_loop

        def _patched_uvloop_set_event_loop(self: typing.Any, loop: typing.Optional[asyncio.AbstractEventLoop]) -> None:
            thread_id = typing.cast(int, ddtrace_threading.current_thread().ident)
            if init_stack:
                stack.set_uvloop_mode(thread_id, True)
            if init_stack and loop is not None:
                stack.track_asyncio_loop(thread_id, loop)
                _call_init_asyncio(asyncio)
            _original_uvloop_sel(self, loop)

        policy_class.set_event_loop = _patched_uvloop_set_event_loop
