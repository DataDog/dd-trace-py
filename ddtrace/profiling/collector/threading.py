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


_thread_hooks_installed: bool = False


def _stack_hooks_active() -> bool:
    return bool(config.stack.enabled and stack.is_available)


# Patch threading.Thread so the stack sampler can track thread lifetimes.
# Existing threads are registered separately, after the sampler's one-time setup.
def install_thread_hooks() -> None:
    global _thread_hooks_installed

    if _thread_hooks_installed or not _stack_hooks_active():
        return

    _thread_hooks_installed = True

    _thread_set_native_id = typing.cast(
        typing.Callable[[threading.Thread], None],
        ddtrace_threading.Thread._set_native_id,  # type: ignore[attr-defined]
    )
    _thread_bootstrap_inner = typing.cast(
        typing.Callable[[threading.Thread], None],
        ddtrace_threading.Thread._bootstrap_inner,  # type: ignore[attr-defined]
    )

    def thread_set_native_id(self: threading.Thread) -> None:
        _thread_set_native_id(self)
        if self.ident is not None and self.native_id is not None:
            stack.register_thread(self.ident, self.native_id, self.name)

    def thread_bootstrap_inner(self: threading.Thread, *args: typing.Any, **kwargs: typing.Any) -> None:
        _thread_bootstrap_inner(self, *args, **kwargs)
        if self.ident is not None:
            stack.unregister_thread(self.ident)

    ddtrace_threading.Thread._set_native_id = thread_set_native_id  # type: ignore[attr-defined]
    ddtrace_threading.Thread._bootstrap_inner = thread_bootstrap_inner  # type: ignore[attr-defined]

    # Import _faulthandler to ensure faulthandler.enable wrapper is initialised.
    # This reinstalls our SIGSEGV handler when faulthandler overwrites it.
    # Import _asyncio to ensure asyncio post-import wrappers are initialised.
    from ddtrace.profiling import _asyncio  # noqa: F401
    from ddtrace.profiling import _faulthandler  # noqa: F401


def register_existing_threads() -> None:
    if not _stack_hooks_active():
        return

    from ddtrace.profiling import _asyncio
    from ddtrace.profiling._threading import get_thread_native_id

    for thread_id, thread in ddtrace_threading._active.items():  # type: ignore[attr-defined]
        stack.register_thread(thread_id, get_thread_native_id(thread_id), thread.name)

    _asyncio.link_existing_loop_to_current_thread()


def init_stack() -> None:
    install_thread_hooks()
    register_existing_threads()
