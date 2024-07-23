from __future__ import absolute_import

import threading
import typing  # noqa:F401

from . import _lock


class ThreadingLockAcquireEvent(_lock.LockAcquireEvent):
    """A threading.Lock has been acquired."""

    __slots__ = ()


class ThreadingLockReleaseEvent(_lock.LockReleaseEvent):
    """A threading.Lock has been released."""

    __slots__ = ()


class _ProfiledThreadingLock(_lock._ProfiledLock):
    ACQUIRE_EVENT_CLASS = ThreadingLockAcquireEvent
    RELEASE_EVENT_CLASS = ThreadingLockReleaseEvent


class ThreadingLockCollector(_lock.LockCollector):
    """Record threading.Lock usage."""

    PROFILED_LOCK_CLASS = _ProfiledThreadingLock

    def _get_original(self):
        # type: (...) -> typing.Any
        return threading.Lock

    def _set_original(
        self, value  # type: typing.Any
    ):
        # type: (...) -> None
        threading.Lock = value  # type: ignore[misc]
