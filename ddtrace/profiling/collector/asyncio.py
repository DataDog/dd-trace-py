from dataclasses import dataclass
import typing  # noqa:F401

from .. import collector
from . import _lock


@dataclass(slots=True)
class AsyncioLockAcquireEvent(_lock.LockAcquireEvent):
    """An asyncio.Lock has been acquired."""


@dataclass(slots=True)
class AsyncioLockReleaseEvent(_lock.LockReleaseEvent):
    """An asyncio.Lock has been released."""


class _ProfiledAsyncioLock(_lock._ProfiledLock):
    ACQUIRE_EVENT_CLASS = AsyncioLockAcquireEvent
    RELEASE_EVENT_CLASS = AsyncioLockReleaseEvent


@dataclass
class AsyncioLockCollector(_lock.LockCollector):
    """Record asyncio.Lock usage."""

    PROFILED_LOCK_CLASS = _ProfiledAsyncioLock

    def _start_service(self):
        # type: (...) -> None
        """Start collecting lock usage."""
        try:
            import asyncio
        except ImportError as e:
            raise collector.CollectorUnavailable(e)
        self._asyncio_module = asyncio
        return super(AsyncioLockCollector, self)._start_service()

    def _get_original(self):
        # type: (...) -> typing.Any
        return self._asyncio_module.Lock

    def _set_original(
        self, value  # type: typing.Any
    ):
        # type: (...) -> None
        self._asyncio_module.Lock = value  # type: ignore[misc]
