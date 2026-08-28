from __future__ import annotations

import asyncio
from types import ModuleType

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
