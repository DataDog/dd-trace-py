from __future__ import annotations

import asyncio
from types import ModuleType


try:
    from . import _lock

    class _ProfiledAsyncioLock(_lock._ProfiledLock):
        pass

    class _ProfiledAsyncioSemaphore(_lock._ProfiledLock):
        pass

    class _ProfiledAsyncioBoundedSemaphore(_lock._ProfiledLock):
        pass

    class _ProfiledAsyncioCondition(_lock._ProfiledLock):
        pass

    class AsyncioLockCollector(_lock.LockCollector):
        """Record asyncio.Lock usage."""

        PROFILED_LOCK_CLASS: type[_ProfiledAsyncioLock] = _ProfiledAsyncioLock
        MODULE: ModuleType = asyncio
        PATCHED_LOCK_NAME: str = "Lock"

    class AsyncioSemaphoreCollector(_lock.LockCollector):
        """Record asyncio.Semaphore usage."""

        PROFILED_LOCK_CLASS: type[_ProfiledAsyncioSemaphore] = _ProfiledAsyncioSemaphore
        MODULE: ModuleType = asyncio
        PATCHED_LOCK_NAME: str = "Semaphore"

    class AsyncioBoundedSemaphoreCollector(_lock.LockCollector):
        """Record asyncio.BoundedSemaphore usage."""

        PROFILED_LOCK_CLASS: type[_ProfiledAsyncioBoundedSemaphore] = _ProfiledAsyncioBoundedSemaphore
        MODULE: ModuleType = asyncio
        PATCHED_LOCK_NAME: str = "BoundedSemaphore"

    class AsyncioConditionCollector(_lock.LockCollector):
        """Record asyncio.Condition usage."""

        PROFILED_LOCK_CLASS: type[_ProfiledAsyncioCondition] = _ProfiledAsyncioCondition
        MODULE: ModuleType = asyncio
        PATCHED_LOCK_NAME: str = "Condition"

except ImportError:
    # TODO(py-315): _lock is a Cython extension that is not compiled for all Python
    # versions (e.g. Python 3.15 before the manylinux image carries it). When it
    # is absent the asyncio lock collectors are unavailable. Defining stubs that
    # raise CollectorUnavailable lets profiler.py discover and gracefully skip them
    # rather than failing at import time.
    from ddtrace.profiling.collector import Collector as _Collector
    from ddtrace.profiling.collector import CollectorUnavailable as _CollectorUnavailable

    class AsyncioLockCollector(_Collector):  # type: ignore[no-redef]
        def start(self) -> None:
            raise _CollectorUnavailable

    AsyncioSemaphoreCollector = AsyncioLockCollector  # type: ignore[assignment,misc]
    AsyncioBoundedSemaphoreCollector = AsyncioLockCollector  # type: ignore[assignment,misc]
    AsyncioConditionCollector = AsyncioLockCollector  # type: ignore[assignment,misc]
