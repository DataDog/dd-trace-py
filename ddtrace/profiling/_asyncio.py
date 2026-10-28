from __future__ import annotations

import contextvars
from functools import partial
import sys
from types import CodeType
from types import FunctionType
from types import ModuleType
import typing
import weakref

import wrapt


if typing.TYPE_CHECKING:
    import asyncio
    import asyncio as aio

    from ddtrace.internal import monitoring as _monitoring

from ddtrace.internal._unpatched import _threading as ddtrace_threading
from ddtrace.internal.datadog.profiling import context_meta
from ddtrace.internal.datadog.profiling import stack
from ddtrace.internal.module import ModuleWatchdog
from ddtrace.internal.native._native import DD_CONTEXTVAR
from ddtrace.internal.settings.profiling import config
from ddtrace.internal.utils import get_argument_value
from ddtrace.internal.utils import set_argument_value
from ddtrace.internal.wrapping import get_wrapped
from ddtrace.internal.wrapping import wrap
from ddtrace.profiling import _span_links


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


# Span links for asyncio tasks.
#
# The stack profiler keeps a native map from each execution unit (a thread, an asyncio task or a greenlet) to the span
# it runs. A task's entry is keyed by id(task), the address of the task object, which is also the id the sampler reads
# for the task. This module writes those task keys in three places:
#
# - the activation listener of the stack collector, through current_task_link_id(), when a span is activated inside a
#   task;
# - the creation hook on loop.create_task, so that a new task starts with the span it inherited from its creator;
# - the first step of an eager task, which runs inside create_task before the creation hook can see the task.
#
# Every task that gets a key also gets a weakref whose callback erases the key when the task object dies, so a key
# never outlives its task and a later object at the same address never sees it.

# id(task) -> a weakref to the task, for every task that may have a key. The weakref's callback erases the key.
_task_refs: dict[int, weakref.ref[typing.Any]] = {}


def _on_task_collected(
    task_id: int,
    task_refs: dict[int, weakref.ref[typing.Any]],
    clear_task: typing.Callable[[int], None],
    _ref: weakref.ref[typing.Any],
) -> None:
    # This runs while the task object is freed, possibly during interpreter shutdown when module globals are already
    # gone. So everything it needs is bound into the callback, and nothing may escape.
    try:
        task_refs.pop(task_id, None)
        clear_task(task_id)
    except Exception:  # nosec B110 -- a profiler failure must not change application behavior
        pass


def ensure_task_cleanup(task: object) -> bool:
    """Make sure the key of task is erased when task dies, and return whether a task key may be written.

    The cleanup hook is installed at most once per task, so a long-lived task that writes many spans keeps one hook.
    It returns False only when the weakref cannot be made (in practice MemoryError). Then the caller must not write a
    key, because nothing would erase it.
    """
    task_id = id(task)
    if task_id in _task_refs:
        return True
    try:
        _task_refs[task_id] = weakref.ref(task, partial(_on_task_collected, task_id, _task_refs, stack.clear_task))
    except Exception:
        return False
    return True


# A function that returns the task that runs on this thread right now, or None.
_RunningTaskReader = typing.Callable[[], typing.Optional["asyncio.Task[typing.Any]"]]


def _no_running_task() -> typing.Optional[asyncio.Task[typing.Any]]:
    return None


# Return the task that runs on this thread right now, or None. It never raises when no loop runs, and it never calls
# into the tracer. It is replaced once asyncio is imported (see _running_task_reader).
_running_task: _RunningTaskReader = _no_running_task


def _running_task_reader(asyncio: ModuleType) -> _RunningTaskReader:
    # This runs on every span activation, so it avoids asyncio.current_task() with no argument, which raises when no
    # loop runs. asyncio.current_task(loop) is C code from Python 3.12. Before 3.12 it is a Python function, so read
    # the dict it reads directly instead.
    get_running_loop = asyncio.events._get_running_loop
    if sys.version_info >= (3, 12):
        current_task_of_loop = asyncio.current_task
    else:
        current_task_of_loop = asyncio.tasks._current_tasks.get

    def running_task() -> typing.Optional[typing.Any]:
        loop = get_running_loop()
        return None if loop is None else current_task_of_loop(loop)

    return running_task


def current_task_link_id() -> int:
    """Return id(task) for the asyncio task running on this thread, or 0 when no task runs.

    A non-zero id comes with the cleanup hook of ensure_task_cleanup already installed, so the caller may write a key
    for it. Activations outside a task, for example from a plain loop callback, return 0 and write only the thread key.
    """
    task = _running_task()
    if task is None or not ensure_task_cleanup(task):
        return 0
    return id(task)


def _inheriting_provider() -> typing.Optional[object]:
    """Return the context provider whose active span new tasks inherit, or None if tasks should not inherit spans.

    That is None while profiling is stopped, and when the provider has its own active() rule (see
    context_meta.inheritance_mode), because then the profiler cannot tell which span such a task would use.
    """
    tracer = _span_links.lifecycle_tracer
    if tracer is None:
        return None
    provider = tracer.context_provider
    return provider if context_meta.inheritance_mode(provider) else None


def _innermost(f: FunctionType) -> FunctionType:
    # ddtrace's bytecode wrap() changes a function in place and keeps the original code in a hidden copy. Walk every
    # wrapper layer down to that copy.
    while (inner := get_wrapped(f)) is not None:
        f = inner
    return f


def is_eager_task_factory(factory: object) -> bool:
    """Return whether factory, a loop task factory, starts tasks eagerly like asyncio.eager_task_factory. Never raises.

    An eager task runs its first step inside create_task, before create_task returns (Python 3.12+). Every factory
    made by asyncio.create_eager_task_factory, including asyncio.eager_task_factory itself, runs the same inner code
    object, so comparing that code object finds all of them, even when ddtrace wrapped them in place. Other callables
    (classes, functools.partial objects, callable instances, and None for the default factory) return False.
    """
    try:
        if factory is None or not isinstance(factory, FunctionType):
            return False
        eager_task_factory = sys.modules["asyncio"].eager_task_factory
        if factory is eager_task_factory:
            return True
        return _innermost(factory).__code__ is _innermost(eager_task_factory).__code__
    except Exception:
        return False


def _eager_execution_detected(task_factory: object, kwargs: dict[str, typing.Any]) -> bool:
    """Return whether create_task(..., **kwargs) may run the task's first step before it returns."""
    if sys.version_info >= (3, 12):
        # eager_start is a create_task argument from Python 3.14. An explicit True or False wins over the task factory.
        eager_start = kwargs.get("eager_start")
        if eager_start is not None:
            return bool(eager_start)
        return is_eager_task_factory(task_factory)
    return False


def _seed_running_task() -> None:
    """Write the span the running task inherited to its key. This runs at the start of an eager task's first step."""
    token = stack.lifecycle()
    # Profiling or the provider may have changed since create_task decided to wrap the coroutine.
    provider = _inheriting_provider()
    if provider is None:
        return
    task = _running_task()
    if task is not None and ensure_task_cleanup(task):
        # The first step runs inside the task's own Context, so the ContextVar holds what the task inherited.
        item = context_meta.resolve(DD_CONTEXTVAR.get())
        _span_links.publish(provider, token, False, id(task), 0, item)


class _EagerTaskCoroutine:
    """Wrap the coroutine of an eager task, so its first step starts by writing the span the task inherited.

    An eager task runs its first step inside create_task, before the creation hook sees the task, and it may even
    complete there. The task runs the wrapper coroutine instead of the original one, so profiles show the wrapper's
    frame, _eager_task_span_link, below the task's own frames for the task's whole life. The asyncio tracing
    integration's traced_coro frame shows up in the same way.
    """

    __slots__ = ("coro", "entered")

    def __init__(self, coro: typing.Coroutine[typing.Any, typing.Any, typing.Any]) -> None:
        self.coro = coro
        # Whether the task ran the first step of the wrapper, and so started the original coroutine.
        self.entered = False

    def wrapper(self) -> typing.Coroutine[typing.Any, typing.Any, typing.Any]:
        """Return the wrapper coroutine to pass to create_task instead of the original one."""
        wrapper = self._eager_task_span_link()
        # Custom task factories may name tasks after their coroutine.
        for attribute in ("__name__", "__qualname__"):
            try:
                setattr(wrapper, attribute, getattr(self.coro, attribute))
            except Exception:  # nosec B110 -- a profiler failure must not change application behavior
                pass
        return wrapper

    async def _eager_task_span_link(self) -> typing.Any:
        self.entered = True
        try:
            _seed_running_task()
        except Exception:  # nosec B110 -- a profiler failure must not change application behavior
            pass
        return await self.coro

    def on_create_task_failed(self, wrapper: typing.Coroutine[typing.Any, typing.Any, typing.Any]) -> None:
        """Close the unused wrapper after create_task raised, as if the original coroutine had been passed."""
        # A factory that closes a coroutine it rejects has closed the wrapper (cr_frame is None after close), so it
        # would have closed the original too. Otherwise the caller still owns the original, and it stays unchanged.
        if typing.cast(typing.Any, wrapper).cr_frame is None:
            self.coro.close()
        wrapper.close()

    def close_if_never_entered(self, _task: object) -> None:
        """Done callback: close the original coroutine if the task ended before its first step."""
        # A task cancelled before its first step throws into the unstarted wrapper, which then never starts the
        # original coroutine. Without the wrapper, that throw would have closed the original, so close it here. This
        # also avoids a "coroutine was never awaited" warning about user code.
        if not self.entered:
            self.coro.close()


def _seed_created_task(
    task: typing.Any,
    kwargs: dict[str, typing.Any],
    custom_task_factory: bool,
) -> None:
    """Write the span that a task created by create_task(..., **kwargs) inherited to the task's key."""
    token = stack.lifecycle()
    # Read the provider after the token: profiling may have restarted, or the provider changed, during create_task.
    provider = _inheriting_provider()
    if provider is None:
        return
    if task.done():
        return  # an eager task that already completed in its first step
    # The span a task inherits is the tracer's ContextVar value in the task's Context.
    if sys.version_info >= (3, 12):
        get_context = getattr(task, "get_context", None)
        if get_context is None:
            return  # a custom factory returned an object whose Context cannot be read
        item = context_meta.resolve(get_context().get(DD_CONTEXTVAR))
    elif custom_task_factory:
        # Before 3.12 the Context of a task cannot be read, and a custom factory may replace it or ignore context=.
        return
    else:
        # Without a factory, the task runs in context= (Python 3.11+) or in a copy of the caller's current Context.
        ctx: typing.Optional[contextvars.Context] = kwargs.get("context")
        item = context_meta.resolve(ctx.get(DD_CONTEXTVAR) if ctx is not None else DD_CONTEXTVAR.get())
    # A task without a span gets no key, so untraced tasks cost no write. No key means no span: a key never outlives
    # its task, so no key left by a dead task at the same address can be found.
    if item is not None and ensure_task_cleanup(task):
        _span_links.publish(provider, token, False, id(task), 0, item)


def _create_task_with_span_link(
    create_task: typing.Callable[..., typing.Any],
    loop: typing.Any,
    coro_index: int,
    args: tuple[typing.Any, ...],
    kwargs: dict[str, typing.Any],
) -> typing.Any:
    """Call create_task(*args, **kwargs) on loop, then write the span the new task inherited from its creator.

    coro_index is the position of the coroutine in args. The asyncio helpers (asyncio.create_task, ensure_future,
    gather, TaskGroup.create_task) all create tasks through loop.create_task, so this covers them. Tasks created with
    asyncio.Task(...) directly are not covered, and get a span only when they activate one.
    """
    try:
        inheriting = _inheriting_provider() is not None
    except Exception:
        inheriting = False
    if not inheriting:
        return create_task(*args, **kwargs)

    custom_task_factory = True
    eager_coroutine: typing.Optional[_EagerTaskCoroutine] = None
    eager_wrapper: typing.Optional[typing.Coroutine[typing.Any, typing.Any, typing.Any]] = None
    try:
        task_factory = loop.get_task_factory()
        custom_task_factory = task_factory is not None
        if _eager_execution_detected(task_factory, kwargs):
            coro = get_argument_value(args, kwargs, coro_index, "coro")
            if sys.modules["asyncio"].iscoroutine(coro):
                eager_coroutine = _EagerTaskCoroutine(
                    typing.cast(typing.Coroutine[typing.Any, typing.Any, typing.Any], coro)
                )
                eager_wrapper = eager_coroutine.wrapper()
                args, kwargs = set_argument_value(args, kwargs, coro_index, "coro", eager_wrapper)
    except Exception:
        if eager_wrapper is not None:
            # The wrapper was made but not passed on. Close it, so it does not warn that it was never awaited.
            eager_wrapper.close()
        eager_coroutine = eager_wrapper = None

    try:
        task = create_task(*args, **kwargs)
    except BaseException:
        if eager_coroutine is not None and eager_wrapper is not None:
            try:
                eager_coroutine.on_create_task_failed(eager_wrapper)
            except Exception:  # nosec B110 -- a profiler failure must not change application behavior
                pass
        raise

    try:
        if eager_coroutine is not None and not eager_coroutine.entered:
            # An empty Context, so the callback does not run in, or change, the application's Context.
            task.add_done_callback(eager_coroutine.close_if_never_entered, context=contextvars.Context())
    except Exception:  # nosec B110 -- a profiler failure must not change application behavior
        pass
    # Seeding runs in its own try, so a failure in eager detection cannot disable it, and the other way around.
    try:
        _seed_created_task(task, kwargs, custom_task_factory)
    except Exception:  # nosec B110 -- a profiler failure must not change application behavior
        pass
    return task


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

    thread_id = typing.cast(int, ddtrace_threading.current_thread().ident)
    if (
        sys.version_info >= (3, 13)
        and isinstance(running_loop, asyncio.BaseEventLoop)
        and type(running_loop).run_forever is asyncio.BaseEventLoop.run_forever
    ):
        # The loop may have started before our run_forever hooks existed, or before this thread was registered, so
        # no hook recorded it. BaseEventLoop.run_forever calls _run_forever_cleanup by name, so our cleanup hook still
        # runs when it stops and clears this entry. A subclass that overrides run_forever may never call cleanup, so
        # it takes the configured-slot path below instead. The call is unowned: it does not replace a running loop
        # that a hook already recorded. The configured loop is left as is, so a loop set with set_event_loop keeps it.
        stack.enter_running_loop(thread_id, running_loop, True)
    else:
        # Before 3.13, the running run_forever frame may have started before our hook existed, and we cannot tell, so
        # nothing is guaranteed to clear a running-loop entry when the loop stops. uvloop and other loops never use
        # the running-loop slot. So these loops go into the configured slot, as set_event_loop would do.
        uvloop_module: typing.Optional[ModuleType] = sys.modules.get("uvloop")
        if (
            config.stack.uvloop  # pyright: ignore[reportAttributeAccessIssue]
            and uvloop_module is not None
            and isinstance(running_loop, uvloop_module.Loop)
        ):
            # Turning uvloop support off (DD_PROFILING_STACK_UVLOOP=false) also turns off uvloop unwinding here.
            stack.set_uvloop_mode(thread_id, True)
        stack.track_asyncio_loop(thread_id, running_loop)
    _call_init_asyncio(asyncio)


def _install_running_loop_hooks(asyncio: ModuleType) -> None:
    """Record, in the native thread record, which plain asyncio loop is running on the thread right now.

    The sampler renders the tasks of a thread's loop. The configured loop (set by set_event_loop or
    uvloop.new_event_loop) misses loops that run without being set, for example asyncio.Runner(loop_factory=...) or a
    bare loop.run_until_complete(). So the hooks below fill a separate running-loop slot while a BaseEventLoop runs.

    The hooks never check whether profiling is running. Thread records outlive a profiler stop, so an exit skipped
    while stopped would leave a stale entry behind.
    """
    base_event_loop: type[typing.Any] = asyncio.base_events.BaseEventLoop

    if sys.version_info >= (3, 13):
        # run_forever calls these two methods by name, and custom loops (for example GUI integrations) may call them
        # without run_forever. Setup raises before it changes anything when the loop is closed or a loop already
        # runs on the thread, and run_forever skips cleanup when setup raised. So registering after a successful
        # setup and clearing before cleanup only ever touches a loop that really runs.

        @partial(wrap, base_event_loop._run_forever_setup)
        def _(f: typing.Callable[..., None], args: tuple[typing.Any, ...], kwargs: dict[str, typing.Any]) -> None:
            result = f(*args, **kwargs)
            try:
                stack.enter_running_loop(typing.cast(int, ddtrace_threading.current_thread().ident), args[0])
            except Exception:  # nosec B110 -- a profiler failure must not change application behavior
                pass
            return result

        @partial(wrap, base_event_loop._run_forever_cleanup)
        def _(f: typing.Callable[..., None], args: tuple[typing.Any, ...], kwargs: dict[str, typing.Any]) -> None:
            try:
                stack.exit_running_loop(typing.cast(int, ddtrace_threading.current_thread().ident), args[0])
            except Exception:  # nosec B110 -- a profiler failure must not change application behavior
                pass
            return f(*args, **kwargs)

    else:

        @partial(wrap, base_event_loop.run_forever)
        def _(f: typing.Callable[..., None], args: tuple[typing.Any, ...], kwargs: dict[str, typing.Any]) -> None:
            loop: typing.Any = args[0]
            thread_id: int = 0
            outermost_run = False
            try:
                thread_id = typing.cast(int, ddtrace_threading.current_thread().ident)
                # run_forever raises when the loop is closed, already running, or when another loop runs on this
                # thread (a nested run). Such a call must not touch the slot of the loop that does run. When the
                # check passes, this is the outermost run on the thread, so clearing on exit is always right, even
                # if the thread was not registered yet when it started.
                outermost_run = (
                    not loop.is_closed() and not loop.is_running() and asyncio.events._get_running_loop() is None
                )
                if outermost_run:
                    stack.enter_running_loop(thread_id, loop)
            except Exception:  # nosec B110 -- a profiler failure must not change application behavior
                pass
            try:
                return f(*args, **kwargs)
            finally:
                if outermost_run:
                    try:
                        stack.exit_running_loop(thread_id, loop)
                    except Exception:  # nosec B110 -- a profiler failure must not change application behavior
                        pass


def _install_task_span_link_hooks(asyncio: ModuleType) -> None:
    """Let the activation listener find the running task, and seed the span of new tasks in BaseEventLoop.create_task.

    Unlike the running-loop hooks, these do nothing while profiling is stopped.
    """
    global _running_task

    _running_task = _running_task_reader(asyncio)

    @partial(wrap, asyncio.base_events.BaseEventLoop.create_task)
    def _(
        f: typing.Callable[..., typing.Any], args: tuple[typing.Any, ...], kwargs: dict[str, typing.Any]
    ) -> typing.Any:
        return _create_task_with_span_link(f, args[0], 1, args, kwargs)


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

    init_stack: bool = (config.install or config.stack.enabled) and stack.is_available

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
        _install_running_loop_hooks(asyncio)
        _install_task_span_link_hooks(asyncio)

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
        # 3.15+ prefers PY_RETURN; below that _register_return_hook returns False and we wrap().
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

    init_stack: bool = (config.install or config.stack.enabled) and stack.is_available

    if init_stack:

        def _uvloop_create_task(
            f: typing.Callable[..., typing.Any],
            loop: typing.Any,
            args: tuple[typing.Any, ...],
            kwargs: dict[str, typing.Any],
        ) -> typing.Any:
            return _create_task_with_span_link(f, loop, 0, args, kwargs)

        # uvloop.Loop.create_task is a Cython method, which the bytecode wrap() used for BaseEventLoop cannot wrap.
        # A failure here must not skip the loop tracking hooks below.
        try:
            wrapt.wrap_function_wrapper(uvloop, "Loop.create_task", _uvloop_create_task)
        except Exception:  # nosec B110 -- a profiler failure must not change application behavior
            pass

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
