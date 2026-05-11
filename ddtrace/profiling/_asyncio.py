# -*- encoding: utf-8 -*-
from __future__ import annotations

from functools import partial
from functools import wraps
import inspect
import sys
import types
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


ASYNCIO_IMPORTED: bool = False


# Trampoline dispatch table.
# Key: id(original) of the wrapped function (kept alive by the module/class
# attribute pointing at it; its identity is preserved across wraps).
# Value: (user_wrapper, original_copy)
_WRAP_REGISTRY: dict[int, tuple[typing.Callable[..., typing.Any], types.FunctionType]] = {}


def _ddtrace_dispatch_wrap(target_id: int, args: tuple[typing.Any, ...], kwargs: dict[str, typing.Any]) -> typing.Any:
    """Sync dispatcher invoked by a wrapped function's trampoline bytecode."""
    wrapper, original_copy = _WRAP_REGISTRY[target_id]
    return wrapper(original_copy, args, kwargs)


async def _ddtrace_dispatch_wrap_async(
    target_id: int, args: tuple[typing.Any, ...], kwargs: dict[str, typing.Any]
) -> typing.Any:
    """Async dispatcher invoked by a wrapped coroutine function's trampoline."""
    wrapper, original_copy = _WRAP_REGISTRY[target_id]
    return await wrapper(original_copy, args, kwargs)


def _wrap(
    owner: typing.Any,
    name: str,
    wrapper: typing.Callable[..., typing.Any],
    aliases: typing.Sequence[tuple[typing.Any, str]] = (),
) -> typing.Callable[..., typing.Any]:
    """Wrap ``owner.name`` so calls go through ``wrapper(original, args, kwargs)``.

    For pure-Python functions (``types.FunctionType``) we mutate the
    original function's ``__code__`` in place to a tiny trampoline that
    dispatches to the user wrapper.  Function identity is preserved, so
    pre-existing captured references (e.g. ``from X import Y`` performed
    before the profiler starts) still see the wrapped behaviour — this
    matches what ``ddtrace.internal.wrapping.wrap`` did via the
    ``bytecode`` library, without taking on that dependency.

    For non-Python callables (Cython methods, C builtins) we fall back to
    ``setattr`` and mirror onto ``aliases``.  ``aliases`` is a no-op on the
    identity-preserving path (both alias bindings already point at the
    same mutated object) and exists only for the fallback case.
    """
    original = getattr(owner, name)

    if isinstance(original, types.FunctionType) and not original.__closure__:
        # Identity-preserving path: mutate __code__ in place.
        # We require the function to have no closure cells — the trampoline
        # we generate has none, and __code__ swaps must match free-var counts.
        # Class methods using super() (e.g. _GatheringFuture.__init__) carry
        # a __class__ closure cell and therefore fall through to setattr.
        original_copy = types.FunctionType(
            original.__code__,
            original.__globals__,
            original.__name__,
            original.__defaults__,
            original.__closure__,
        )
        original_copy.__kwdefaults__ = original.__kwdefaults__

        is_async = inspect.iscoroutinefunction(original)
        target_id = id(original)
        _WRAP_REGISTRY[target_id] = (wrapper, original_copy)

        dispatcher_name = "_ddtrace_dispatch_wrap_async" if is_async else "_ddtrace_dispatch_wrap"
        trampoline_name = original.__name__ if original.__name__.isidentifier() else "_ddtrace_trampoline"

        if is_async:
            source = (
                f"async def {trampoline_name}(*args, **kwargs):\n"
                f"    return await {dispatcher_name}({target_id}, args, kwargs)\n"
            )
        else:
            source = (
                f"def {trampoline_name}(*args, **kwargs):\n    return {dispatcher_name}({target_id}, args, kwargs)\n"
            )

        ns: dict[str, typing.Any] = {}
        # nosec B102: source is built from a fixed template; the only
        # interpolated values are an int (target_id, from id()) and a
        # name validated via .isidentifier(). No untrusted input.
        exec(source, ns)  # nosec B102
        trampoline = ns[trampoline_name]

        # The trampoline uses LOAD_GLOBAL for dispatcher_name, resolved
        # against original's module globals at call time.  Inject the
        # dispatcher there (idempotently — many functions in the same
        # module share one entry).
        original.__globals__.setdefault(dispatcher_name, globals()[dispatcher_name])

        # Preserve filename / firstlineno / co_name for stack-trace clarity.
        new_code = trampoline.__code__.replace(
            co_filename=original.__code__.co_filename,
            co_firstlineno=original.__code__.co_firstlineno,
            co_name=original.__code__.co_name,
        )
        original.__code__ = new_code
        return original

    # Fallback for Cython / C builtins or Python functions with closure
    # cells (e.g. class methods using ``super()``).  Identity isn't
    # preserved here; callers that also need to patch aliased bindings
    # must pass them via ``aliases``.
    @wraps(original)
    def wrapped(*args: typing.Any, **kwargs: typing.Any) -> typing.Any:
        return wrapper(original, args, kwargs)

    setattr(owner, name, wrapped)
    for alias_owner, alias_name in aliases:
        setattr(alias_owner, alias_name, wrapped)
    return wrapped


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

        @partial(_wrap, policy_class, "set_event_loop")  # pyright: ignore[reportArgumentType]
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

        @partial(_wrap, sys.modules["asyncio"].tasks._GatheringFuture, "__init__")
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

        @partial(_wrap, sys.modules["asyncio"].tasks, "_wait")
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

        @partial(
            _wrap,
            sys.modules["asyncio"].tasks,
            "as_completed",
            aliases=[(sys.modules["asyncio"], "as_completed")],
        )
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
        @partial(
            _wrap,
            sys.modules["asyncio"].tasks,
            "shield",
            aliases=[(sys.modules["asyncio"], "shield")],
        )
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

                    @partial(_wrap, taskgroup_class, "create_task")
                    def _(
                        f: typing.Callable[..., aio.Task[typing.Any]],
                        args: tuple[typing.Any, ...],
                        kwargs: dict[str, typing.Any],
                    ) -> aio.Task[typing.Any]:
                        result: aio.Task[typing.Any] = f(*args, **kwargs)

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
        @partial(
            _wrap,
            sys.modules["asyncio"].tasks,
            "create_task",
            aliases=[(sys.modules["asyncio"], "create_task")],
        )
        def _(
            f: typing.Callable[..., aio.Task[typing.Any]],
            args: tuple[typing.Any, ...],
            kwargs: dict[str, typing.Any],
        ) -> aio.Task[typing.Any]:
            # kwargs will typically contain context (Python 3.11+ only) and eager_start (Python 3.14+ only)
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

        @partial(_wrap, uvloop, "new_event_loop")
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

        @partial(_wrap, policy_class, "set_event_loop")  # pyright: ignore[reportArgumentType]
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
