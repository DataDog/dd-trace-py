import asyncio
from types import ModuleType
from typing import Type

from . import _lock


class _ProfiledAsyncioLock(_lock._ProfiledLock):
    pass


class _ProfiledAsyncioSemaphore(_lock._ProfiledLock):
    pass


class AsyncioLockCollector(_lock.LockCollector):
    """Record asyncio.Lock usage."""

    PROFILED_LOCK_CLASS: Type[_ProfiledAsyncioLock] = _ProfiledAsyncioLock
    MODULE: ModuleType = asyncio
    PATCHED_LOCK_NAME: str = "Lock"


class AsyncioSemaphoreCollector(_lock.LockCollector):
    """Record asyncio.Semaphore usage."""

    PROFILED_LOCK_CLASS: Type[_ProfiledAsyncioSemaphore] = _ProfiledAsyncioSemaphore
    MODULE: ModuleType = asyncio
    PATCHED_LOCK_NAME: str = "Semaphore"
