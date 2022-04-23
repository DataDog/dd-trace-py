import typing

import attr

from . import _lock
from .. import collector
from .. import event


@event.event_class
class AsyncioLockAcquireEvent(_lock.LockAcquireEvent):
    """An asyncio.Lock has been acquired."""


@event.event_class
class AsyncioLockReleaseEvent(_lock.LockReleaseEvent):
    """An asyncio.Lock has been released."""


class _ProfiledAsyncioLock(_lock._ProfiledLock):

    ACQUIRE_EVENT_CLASS = AsyncioLockAcquireEvent
    RELEASE_EVENT_CLASS = AsyncioLockReleaseEvent


@attr.s
class AsyncioLockCollector(_lock.LockCollector):
    """Record asyncio.Lock usage."""

    PROFILED_LOCK_CLASS = _ProfiledAsyncioLock

    def _start_service(self):  # type: ignore[override]
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
