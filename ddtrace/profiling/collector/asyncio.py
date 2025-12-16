import asyncio
import asyncio.locks

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
    # Detect internal locks created by asyncio.Condition
    INTERNAL_MODULE_FILE = asyncio.locks.__file__


class AsyncioSemaphoreCollector(_lock.LockCollector):
    """Record asyncio.Semaphore usage."""

    PROFILED_LOCK_CLASS = _ProfiledAsyncioSemaphore
    MODULE = asyncio
    PATCHED_LOCK_NAME = "Semaphore"


class AsyncioBoundedSemaphoreCollector(_lock.LockCollector):
    """Record asyncio.BoundedSemaphore usage."""

    PROFILED_LOCK_CLASS = _ProfiledAsyncioBoundedSemaphore
    MODULE = asyncio
    PATCHED_LOCK_NAME = "BoundedSemaphore"


class AsyncioConditionCollector(_lock.LockCollector):
    """Record asyncio.Condition usage."""

    PROFILED_LOCK_CLASS = _ProfiledAsyncioCondition
    MODULE = asyncio
    PATCHED_LOCK_NAME = "Condition"
