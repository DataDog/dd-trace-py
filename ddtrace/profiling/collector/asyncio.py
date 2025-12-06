from __future__ import annotations

import asyncio

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

    PROFILED_LOCK_CLASS = _ProfiledAsyncioLock
    MODULE = asyncio
    PATCHED_LOCK_NAME = "Lock"


class AsyncioSemaphoreCollector(_lock.LockCollector):
    """Record asyncio.Semaphore usage."""

    PROFILED_LOCK_CLASS: Type[_ProfiledAsyncioSemaphore] = _ProfiledAsyncioSemaphore
    MODULE: ModuleType = asyncio
    PATCHED_LOCK_NAME: str = "Semaphore"


class AsyncioBoundedSemaphoreCollector(_lock.LockCollector):
    """Record asyncio.BoundedSemaphore usage."""

    PROFILED_LOCK_CLASS = _ProfiledAsyncioBoundedSemaphore
    MODULE = asyncio
    PATCHED_LOCK_NAME = "BoundedSemaphore"
