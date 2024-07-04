from __future__ import absolute_import

from dataclasses import dataclass
import threading
import typing  # noqa:F401

from ddtrace.internal.compat import dataclass_slots

from . import _lock


@dataclass(**dataclass_slots())
class ThreadingLockAcquireEvent(_lock.LockAcquireEvent):
    """A threading.Lock has been acquired."""


@dataclass(**dataclass_slots())
class ThreadingLockReleaseEvent(_lock.LockReleaseEvent):
    """A threading.Lock has been released."""


class _ProfiledThreadingLock(_lock._ProfiledLock):
    ACQUIRE_EVENT_CLASS = ThreadingLockAcquireEvent
    RELEASE_EVENT_CLASS = ThreadingLockReleaseEvent


@dataclass
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
