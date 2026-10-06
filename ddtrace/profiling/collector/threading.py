import threading
from types import ModuleType
import typing

from ddtrace.internal._unpatched import _threading as ddtrace_threading
from ddtrace.internal.datadog.profiling import stack
from ddtrace.internal.settings.profiling import config

from . import _lock


class _ProfiledThreadingLock(_lock._ProfiledLock):
    pass


class _ProfiledThreadingRLock(_lock._ProfiledLock):
    pass


class _ProfiledThreadingSemaphore(_lock._ProfiledLock):
    pass


class _ProfiledThreadingBoundedSemaphore(_lock._ProfiledLock):
    pass


class _ProfiledThreadingCondition(_lock._ProfiledLock):
    pass


class ThreadingLockCollector(_lock.LockCollector):
    """Record threading.Lock usage."""

    PROFILED_LOCK_CLASS: type[_ProfiledThreadingLock] = _ProfiledThreadingLock
    MODULE: ModuleType = threading
    PATCHED_LOCK_NAME: str = "Lock"


class ThreadingRLockCollector(_lock.LockCollector):
    """Record threading.RLock usage."""

    PROFILED_LOCK_CLASS: type[_ProfiledThreadingRLock] = _ProfiledThreadingRLock
    MODULE: ModuleType = threading
    PATCHED_LOCK_NAME: str = "RLock"


class ThreadingSemaphoreCollector(_lock.LockCollector):
    """Record threading.Semaphore usage."""

    PROFILED_LOCK_CLASS: type[_ProfiledThreadingSemaphore] = _ProfiledThreadingSemaphore
    MODULE: ModuleType = threading
    PATCHED_LOCK_NAME: str = "Semaphore"


class ThreadingBoundedSemaphoreCollector(_lock.LockCollector):
    """Record threading.BoundedSemaphore usage."""

    PROFILED_LOCK_CLASS: type[_ProfiledThreadingBoundedSemaphore] = _ProfiledThreadingBoundedSemaphore
    MODULE: ModuleType = threading
    PATCHED_LOCK_NAME: str = "BoundedSemaphore"


class ThreadingConditionCollector(_lock.LockCollector):
    """Record threading.Condition usage."""

    PROFILED_LOCK_CLASS: type[_ProfiledThreadingCondition] = _ProfiledThreadingCondition
    MODULE: ModuleType = threading
    PATCHED_LOCK_NAME: str = "Condition"


# Latest hook installed for each Thread method, keyed by method name.
_installed_thread_hooks: dict[str, typing.Callable[..., None]] = {}

# Method names whose hook is currently running in this thread.
_active_thread_hooks = ddtrace_threading.local()


def _install_thread_hook(name: str, after: typing.Callable[[threading.Thread], None]) -> None:
    Thread = ddtrace_threading.Thread
    original = typing.cast(typing.Callable[..., None], getattr(Thread, name))
    # The hooks are never removed, so installing them again on every profiler restart would nest
    # another layer of wrappers around each thread start and exit.
    if original is _installed_thread_hooks.get(name):
        return

    def hook(self: threading.Thread, *args: typing.Any, **kwargs: typing.Any) -> None:
        # Another library may have wrapped a previous hook, which then stays in the call chain beneath the new one.
        # Only the outermost hook of the call chain acts so that each thread is registered and unregistered once.
        # This is decided per call, because a thread that started before a restart only has the previous hooks
        # in its call chain.
        if getattr(_active_thread_hooks, name, False):
            original(self, *args, **kwargs)
            return

        setattr(_active_thread_hooks, name, True)
        try:
            original(self, *args, **kwargs)
        finally:
            setattr(_active_thread_hooks, name, False)

        after(self)

    _installed_thread_hooks[name] = hook
    setattr(Thread, name, hook)


def _register_thread(thread: threading.Thread) -> None:
    if thread.ident is not None and thread.native_id is not None:
        stack.register_thread(thread.ident, thread.native_id, thread.name)


def _unregister_thread(thread: threading.Thread) -> None:
    if thread.ident is not None:
        stack.unregister_thread(thread.ident)


def _install_thread_hooks() -> None:
    _install_thread_hook("_set_native_id", _register_thread)
    _install_thread_hook("_bootstrap_inner", _unregister_thread)


# Also patch threading.Thread so echion can track thread lifetimes
def init_stack() -> None:
    if (config.install or config.stack.enabled) and stack.is_available:
        from ddtrace.profiling._threading import get_thread_native_id

        _install_thread_hooks()

        # Instrument any living threads
        for thread_id, thread in ddtrace_threading._active.items():  # type: ignore[attr-defined]
            stack.register_thread(thread_id, get_thread_native_id(thread_id), thread.name)

        # Import _faulthandler to ensure faulthandler.enable wrapper is initialised.
        # This reinstalls our SIGSEGV handler when faulthandler overwrites it.
        # Import _asyncio to ensure asyncio post-import wrappers are initialised
        from ddtrace.profiling import _asyncio  # noqa: F401
        from ddtrace.profiling import _faulthandler  # noqa: F401

        _asyncio.link_existing_loop_to_current_thread()
