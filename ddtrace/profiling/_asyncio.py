# -*- encoding: utf-8 -*-
"""Publish tracing attribution for asyncio tasks sampled independently from their event-loop thread.

Task-creation hooks seed native task links from inherited profiler ContextVar state. Span activations update the
current task through the shared provider, so mappings remain correct across scheduler switches without a switch hook.
"""

from __future__ import annotations

import contextvars
from functools import partial
import sys
from types import ModuleType
import typing
import weakref

import wrapt


if typing.TYPE_CHECKING:
    import asyncio
    import asyncio as aio

from ddtrace.internal import forksafe
from ddtrace.internal._unpatched import _threading as ddtrace_threading
from ddtrace.internal.datadog.profiling import stack
from ddtrace.internal.module import ModuleWatchdog
from ddtrace.internal.settings.profiling import config
from ddtrace.internal.utils import get_argument_value
from ddtrace.internal.wrapping import wrap
from ddtrace.profiling import _span_links


ASYNCIO_IMPORTED: bool = False
_TASK_CONTEXT_IS_READABLE = sys.version_info >= (3, 12)
# The finalizer registry both deduplicates nested creation hooks and owns cleanup until each task completes.
_task_span_finalizers: dict[int, weakref.finalize[..., typing.Any]] = {}


def _clear_native_task_span(task_id: int) -> None:
    _span_links.clear_logical_span(_span_links.SpanLinkDomain.ASYNCIO_TASK, task_id)


def _finalize_task_span(task_id: int) -> None:
    _task_span_finalizers.pop(task_id, None)
    try:
        _clear_native_task_span(task_id)
    except Exception:  # nosec B110
        # Weakref callbacks can run while module globals are being cleared during interpreter shutdown.
        pass


def _ensure_task_span_finalizer(task: asyncio.Task[typing.Any]) -> bool:
    """Install prompt and GC-fallback cleanup, returning whether attribution can safely be retained."""
    task_id = id(task)
    if task_id in _task_span_finalizers:
        return True
    try:
        finalizer = weakref.finalize(task, _finalize_task_span, task_id)
    except TypeError:
        return False
    finalizer.atexit = False
    _task_span_finalizers[task_id] = finalizer
    try:
        task.add_done_callback(lambda _: finalizer(), context=contextvars.Context())
    except Exception:
        _task_span_finalizers.pop(task_id, None)
        finalizer.detach()
        return False
    return True


def _reset_task_span_state_after_fork() -> None:
    for finalizer in _task_span_finalizers.values():
        finalizer.detach()
    _task_span_finalizers.clear()


forksafe.register(_reset_task_span_state_after_fork)


def _track_asyncio_loop(thread_id: int, loop: typing.Optional[asyncio.AbstractEventLoop]) -> None:
    try:
        stack.track_asyncio_loop(thread_id, loop)
    except Exception:
        return


def _current_task_span_target() -> typing.Optional[_span_links.LogicalSpanTarget]:
    """Return the current task only when the native sampler can render its logical stack."""
    if get_running_loop() is None:
        return None
    thread_id = ddtrace_threading.current_thread().ident
    if thread_id is None or not stack.is_asyncio_loop_registered(thread_id):
        return None
    try:
        task = current_task()
    except RuntimeError:
        return None
    if task is None or not _ensure_task_span_finalizer(task):
        return None
    return _span_links.LogicalSpanTarget(_span_links.SpanLinkDomain.ASYNCIO_TASK, id(task))


def _has_custom_task_factory(loop: asyncio.AbstractEventLoop) -> bool:
    if _TASK_CONTEXT_IS_READABLE:
        return False
    try:
        return loop.get_task_factory() is not None
    except Exception:
        # Publication is best effort and must never make application task creation fail.
        return True


def _publish_task_span(
    task: asyncio.Task[typing.Any],
    requested_context: typing.Optional[contextvars.Context],
    had_custom_task_factory: bool,
) -> None:
    """Seed a task link from its actual or verifiably inherited profiler context."""
    task_type = getattr(sys.modules.get("asyncio"), "Task", None)
    task_id = id(task)
    if task_type is None or not isinstance(task, task_type) or task_id in _task_span_finalizers:
        return

    task_context = requested_context
    get_context = getattr(task, "get_context", None)
    if get_context is not None:
        try:
            task_context = typing.cast("contextvars.Context", get_context())
        except Exception:
            return
    elif had_custom_task_factory:
        # Before Python 3.12 there is no API for reading the Task's actual Context. A custom factory can replace the
        # creator's Context, so omit attribution rather than publishing unverifiable inherited metadata.
        return

    try:
        published = _span_links.link_logical_span_context(
            _span_links.SpanLinkDomain.ASYNCIO_TASK, task_id, task_context
        )
        if published and not _ensure_task_span_finalizer(task):
            _clear_native_task_span(task_id)
    except Exception:
        return


def _call_and_publish_task(
    f: typing.Callable[..., asyncio.Task[typing.Any]],
    args: tuple[typing.Any, ...],
    kwargs: dict[str, typing.Any],
    loop: typing.Optional[asyncio.AbstractEventLoop],
) -> asyncio.Task[typing.Any]:
    """Preserve task creation semantics, then publish attribution as a best-effort side effect."""
    had_custom_task_factory = loop is None or _has_custom_task_factory(loop)
    task = f(*args, **kwargs)
    _publish_task_span(
        task,
        typing.cast("typing.Optional[contextvars.Context]", kwargs.get("context")),
        had_custom_task_factory,
    )
    return task


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
    """Restore native loop registration when profiling starts late or continues after fork."""
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
    _track_asyncio_loop(typing.cast(int, ddtrace_threading.current_thread().ident), running_loop)
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
                _track_asyncio_loop(typing.cast(int, ddtrace_threading.current_thread().ident), loop)
            return f(*args, **kwargs)

    if init_stack:
        # Asyncio tasks take precedence over gevent when both schedulers run on one physical thread.
        _span_links.register_logical_span_provider(_current_task_span_target, priority=20)

        # ponytail: Direct asyncio.Task(...) construction bypasses loop APIs; add a native construction hook if this
        # discouraged path needs attribution without adding overhead to every event-loop callback.
        base_event_loop_class = sys.modules["asyncio.base_events"].BaseEventLoop

        @partial(wrap, base_event_loop_class.run_forever)
        def _(
            f: typing.Callable[..., None],
            args: tuple[typing.Any, ...],
            kwargs: dict[str, typing.Any],
        ) -> None:
            # Runner(loop_factory=...) and direct run_until_complete() can execute a loop that was never registered
            # through an event-loop policy. Track it only while it is running so stopped loops are not retained.
            loop = typing.cast("aio.AbstractEventLoop", args[0])
            thread_id = typing.cast(int, ddtrace_threading.current_thread().ident)
            _track_asyncio_loop(thread_id, loop)
            try:
                return f(*args, **kwargs)
            finally:
                _track_asyncio_loop(thread_id, None)

        @partial(wrap, base_event_loop_class.create_task)
        def _(
            f: typing.Callable[..., aio.Task[typing.Any]],
            args: tuple[typing.Any, ...],
            kwargs: dict[str, typing.Any],
        ) -> aio.Task[typing.Any]:
            loop = typing.cast("aio.AbstractEventLoop", args[0])
            return _call_and_publish_task(f, args, kwargs, loop)

        def _publish_ensured_future(
            f: typing.Callable[..., aio.Future[typing.Any]],
            args: tuple[typing.Any, ...],
            kwargs: dict[str, typing.Any],
        ) -> aio.Future[typing.Any]:
            awaitable = get_argument_value(args, kwargs, 0, "coro_or_future")
            loop = typing.cast("typing.Optional[aio.AbstractEventLoop]", kwargs.get("loop"))
            if loop is None:
                loop = globals()["get_running_loop"]()
            if loop is None and awaitable is not None:
                try:
                    loop = awaitable.get_loop()
                except Exception:  # nosec B110
                    pass
            had_custom_task_factory = loop is None or _has_custom_task_factory(loop)
            future = f(*args, **kwargs)
            if future is not awaitable:
                _publish_task_span(typing.cast("aio.Task[typing.Any]", future), None, had_custom_task_factory)
            return future

        # The public helper delegates to _ensure_future on supported Python versions. Wrap only the lowest helper to
        # avoid publishing each task once in ensure_future and again in _ensure_future.
        private_ensure_future = getattr(sys.modules["asyncio"].tasks, "_ensure_future", None)
        wrap(
            private_ensure_future or sys.modules["asyncio"].tasks.ensure_future,
            _publish_ensured_future,
        )

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

        # Wrap asyncio.TaskGroup.create_task to link parent task to created tasks (Python 3.11+)
        if sys.hexversion >= 0x030B0000:  # Python 3.11+
            taskgroups_module: typing.Optional[ModuleType] = sys.modules.get("asyncio.taskgroups")
            if taskgroups_module is not None:
                taskgroup_class: typing.Optional[type[typing.Any]] = getattr(taskgroups_module, "TaskGroup", None)
                if taskgroup_class is not None and hasattr(taskgroup_class, "create_task"):

                    @partial(wrap, taskgroup_class.create_task)
                    def _(
                        f: typing.Callable[..., aio.Task[typing.Any]],
                        args: tuple[typing.Any, ...],
                        kwargs: dict[str, typing.Any],
                    ) -> aio.Task[typing.Any]:
                        task_group = args[0]
                        loop = typing.cast("typing.Optional[aio.AbstractEventLoop]", getattr(task_group, "_loop", None))
                        result = _call_and_publish_task(f, args, kwargs, loop)

                        parent: typing.Optional[aio.Task[typing.Any]] = globals()["current_task"]()
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
            f: typing.Callable[..., aio.Task[typing.Any]],
            args: tuple[typing.Any, ...],
            kwargs: dict[str, typing.Any],
        ) -> aio.Task[typing.Any]:
            # kwargs will typically contain context (Python 3.11+ only) and eager_start (Python 3.14+ only)
            loop = globals()["get_running_loop"]()
            task = _call_and_publish_task(f, args, kwargs, loop)
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

    if init_stack:

        def _publish_uvloop_task(
            f: typing.Callable[..., asyncio.Task[typing.Any]],
            loop: asyncio.AbstractEventLoop,
            args: tuple[typing.Any, ...],
            kwargs: dict[str, typing.Any],
        ) -> asyncio.Task[typing.Any]:
            return _call_and_publish_task(f, args, kwargs, loop)

        # uvloop.Loop.create_task is a Cython method, so the bytecode wrapper used above cannot wrap it.
        wrapt.wrap_function_wrapper(uvloop.Loop, "create_task", _publish_uvloop_task)

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

                _track_asyncio_loop(thread_id, loop)
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
                _track_asyncio_loop(typing.cast(int, ddtrace_threading.current_thread().ident), loop)
                _call_init_asyncio(asyncio)

            return f(*args, **kwargs)
