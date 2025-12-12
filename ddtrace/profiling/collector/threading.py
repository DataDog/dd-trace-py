from __future__ import absolute_import

import threading
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


class ThreadingLockCollector(_lock.LockCollector):
    """Record threading.Lock usage."""

    PROFILED_LOCK_CLASS = _ProfiledThreadingLock
    MODULE = threading
    PATCHED_LOCK_NAME = "Lock"


class ThreadingRLockCollector(_lock.LockCollector):
    """Record threading.RLock usage."""

    PROFILED_LOCK_CLASS = _ProfiledThreadingRLock
    MODULE = threading
    PATCHED_LOCK_NAME = "RLock"


class ThreadingSemaphoreCollector(_lock.LockCollector):
    """Record threading.Semaphore usage."""

    PROFILED_LOCK_CLASS = _ProfiledThreadingSemaphore
    MODULE = threading
    PATCHED_LOCK_NAME = "Semaphore"


class ThreadingBoundedSemaphoreCollector(_lock.LockCollector):
    """Record threading.BoundedSemaphore usage."""

    PROFILED_LOCK_CLASS = _ProfiledThreadingBoundedSemaphore
    MODULE = threading
    PATCHED_LOCK_NAME = "BoundedSemaphore"


# Also patch threading.Thread so echion can track thread lifetimes
def init_stack() -> None:
    if config.stack.enabled and stack.is_available:
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

        # Instrument any living threads
        for thread_id, thread in ddtrace_threading._active.items():  # type: ignore[attr-defined]
            stack.register_thread(thread_id, thread.native_id, thread.name)

        # Import _asyncio to ensure asyncio post-import wrappers are initialised
        from ddtrace.profiling import _asyncio  # noqa: F401

        _asyncio.link_existing_loop_to_current_thread()
