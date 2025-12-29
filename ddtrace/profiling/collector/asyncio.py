from __future__ import annotations

import asyncio
import asyncio.locks
from types import ModuleType
from typing import Optional
from typing import Type

from . import _lock


# Internal module file for asyncio lock detection
# This is used to detect locks created internally by Semaphore/Condition
_ASYNCIO_LOCKS_FILE: Optional[str] = asyncio.locks.__file__


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

    PROFILED_LOCK_CLASS: Type[_ProfiledAsyncioLock] = _ProfiledAsyncioLock
    MODULE: ModuleType = asyncio
    PATCHED_LOCK_NAME: str = "Lock"
    INTERNAL_MODULE_FILE: Optional[str] = _ASYNCIO_LOCKS_FILE


class AsyncioSemaphoreCollector(_lock.LockCollector):
    """Record asyncio.Semaphore usage."""

    PROFILED_LOCK_CLASS: Type[_ProfiledAsyncioSemaphore] = _ProfiledAsyncioSemaphore
    MODULE: ModuleType = asyncio
    PATCHED_LOCK_NAME: str = "Semaphore"
    INTERNAL_MODULE_FILE: Optional[str] = _ASYNCIO_LOCKS_FILE


class AsyncioBoundedSemaphoreCollector(_lock.LockCollector):
    """Record asyncio.BoundedSemaphore usage."""

    PROFILED_LOCK_CLASS: Type[_ProfiledAsyncioBoundedSemaphore] = _ProfiledAsyncioBoundedSemaphore
    MODULE: ModuleType = asyncio
    PATCHED_LOCK_NAME: str = "BoundedSemaphore"
    INTERNAL_MODULE_FILE: Optional[str] = _ASYNCIO_LOCKS_FILE


class AsyncioConditionCollector(_lock.LockCollector):
    """Record asyncio.Condition usage."""

    PROFILED_LOCK_CLASS: Type[_ProfiledAsyncioCondition] = _ProfiledAsyncioCondition
    MODULE: ModuleType = asyncio
    PATCHED_LOCK_NAME: str = "Condition"
    INTERNAL_MODULE_FILE: Optional[str] = _ASYNCIO_LOCKS_FILE
