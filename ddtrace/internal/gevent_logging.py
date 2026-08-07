"""Route ddtrace log records from native threads through the owning gevent hub.

When gevent patches threading, logging handler locks become gevent semaphores. A hubless native
thread can acquire such a lock, but releasing it from that thread can permanently strand a
greenlet waiting for the lock. The invariant enforced here is that a ddtrace record created
outside the hub thread never reaches logging.Handler.handle on that native thread.

The logger filter places those records in a bounded queue and calls the hub's thread-safe wakeup.
The hub callback starts a raw greenlet to emit them because logging handlers are allowed to yield.
"""

from _thread import allocate_lock as _allocate_native_lock
from _thread import get_ident as _get_native_ident
import collections
import functools
import logging
from typing import Any
from typing import Callable
from typing import Deque
from typing import Optional
from typing import Protocol

from ddtrace.internal import atexit


class _GeventLoop(Protocol):
    def run_callback_threadsafe(self, callback: Callable[..., object], *args: object) -> object: ...


class _GeventHub(Protocol):
    loop: _GeventLoop


_DEFERRED_LOG_RECORD_LIMIT = 1024
_DEFERRED_LOG_RECORDS: Deque[logging.LogRecord] = collections.deque(maxlen=_DEFERRED_LOG_RECORD_LIMIT)  # noqa: UP006
_DEFERRED_LOG_STATE_LOCK = _allocate_native_lock()
_DEFERRED_RECORD_ATTRIBUTE = "_dd_deferred"
_DEFERRED_RECORD_SENTINEL = object()
_FORKSAFE_HOOK_REGISTERED = False
_DEFERRED_DRAIN_SCHEDULED = False
_OWNER_HUB: Optional[_GeventHub] = None
_SPAWN_RAW_GREENLET: Optional[Callable[..., object]] = None
_HUB_THREAD_IDENT = _get_native_ident()
gevent_threading_patched = False


def is_hub_thread() -> bool:
    return _get_native_ident() == _HUB_THREAD_IDENT


def consume_replay_marker(record: logging.LogRecord) -> bool:
    if getattr(record, _DEFERRED_RECORD_ATTRIBUTE, None) is not _DEFERRED_RECORD_SENTINEL:
        return False
    delattr(record, _DEFERRED_RECORD_ATTRIBUTE)
    return True


def defer_record_to_hub(record: logging.LogRecord) -> None:
    _DEFERRED_LOG_RECORDS.append(record)
    _schedule_drain_on_hub()


def _drain_deferred_records() -> None:
    """Emit one bounded batch of deferred records from a gevent-managed greenlet."""
    global _DEFERRED_DRAIN_SCHEDULED

    try:
        # Bound each drain to the records present on entry so producers cannot starve the hub thread.
        for _ in range(len(_DEFERRED_LOG_RECORDS)):
            try:
                record = _DEFERRED_LOG_RECORDS.popleft()
            except IndexError:
                break
            setattr(record, _DEFERRED_RECORD_ATTRIBUTE, _DEFERRED_RECORD_SENTINEL)
            logging.getLogger(record.name).handle(record)
    finally:
        with _DEFERRED_LOG_STATE_LOCK:
            _DEFERRED_DRAIN_SCHEDULED = False
            schedule_another_drain = bool(_DEFERRED_LOG_RECORDS)
        if schedule_another_drain:
            _schedule_drain_on_hub()


atexit.register(_drain_deferred_records)


def _schedule_drain_on_hub() -> None:
    """Wake the owning gevent hub when a native thread defers a log record."""
    global _DEFERRED_DRAIN_SCHEDULED

    with _DEFERRED_LOG_STATE_LOCK:
        hub = _OWNER_HUB
        spawn_raw_greenlet = _SPAWN_RAW_GREENLET
        if _DEFERRED_DRAIN_SCHEDULED or hub is None or spawn_raw_greenlet is None:
            return
        _DEFERRED_DRAIN_SCHEDULED = True

    try:
        # Loop callbacks cannot yield, but logging handlers can. Start a raw greenlet before invoking them.
        hub.loop.run_callback_threadsafe(spawn_raw_greenlet, _drain_deferred_records)
    except Exception:
        # The hub can already be destroyed during interpreter shutdown. The atexit drain remains as a fallback.
        with _DEFERRED_LOG_STATE_LOCK:
            _DEFERRED_DRAIN_SCHEDULED = False


def _reset_after_fork() -> None:
    global _DEFERRED_LOG_STATE_LOCK, _DEFERRED_DRAIN_SCHEDULED, _HUB_THREAD_IDENT, _OWNER_HUB

    from gevent.hub import get_hub

    _DEFERRED_LOG_RECORDS.clear()
    _DEFERRED_LOG_STATE_LOCK = _allocate_native_lock()
    _DEFERRED_DRAIN_SCHEDULED = False
    _OWNER_HUB = get_hub()
    _HUB_THREAD_IDENT = _get_native_ident()


def _enable_if_threading_patched(gevent_monkey: Any) -> None:
    global _DEFERRED_LOG_STATE_LOCK, _FORKSAFE_HOOK_REGISTERED, _OWNER_HUB, _SPAWN_RAW_GREENLET
    global _HUB_THREAD_IDENT, _allocate_native_lock, _get_native_ident, gevent_threading_patched

    if gevent_threading_patched or not gevent_monkey.is_module_patched("threading"):
        return

    # ddtrace can be imported after monkey.patch_all(), in which case the module-level alias was patched too.
    _allocate_native_lock = gevent_monkey.get_original("_thread", "allocate_lock")
    _get_native_ident = gevent_monkey.get_original("_thread", "get_ident")
    from gevent.hub import get_hub
    from gevent.hub import spawn_raw

    # Capture the patching thread's hub. Calling get_hub() later from a foreign worker would create an unused hub.
    _OWNER_HUB = get_hub()
    _SPAWN_RAW_GREENLET = spawn_raw
    _HUB_THREAD_IDENT = _get_native_ident()
    _DEFERRED_LOG_STATE_LOCK = _allocate_native_lock()
    gevent_threading_patched = True
    if _FORKSAFE_HOOK_REGISTERED:
        return

    from ddtrace.internal import forksafe

    forksafe.register(_reset_after_fork)
    _FORKSAFE_HOOK_REGISTERED = True


def configure_gevent_logging(gevent_monkey: Any) -> None:
    """Route native-thread records through the gevent hub after threading is patched."""
    if getattr(gevent_monkey, "_ddtrace_logging_wrapped", False):
        _enable_if_threading_patched(gevent_monkey)
        return

    original_patch_all = gevent_monkey.patch_all
    original_patch_thread = gevent_monkey.patch_thread

    @functools.wraps(original_patch_all)
    def patch_all(*args: Any, **kwargs: Any) -> Any:
        result = original_patch_all(*args, **kwargs)
        _enable_if_threading_patched(gevent_monkey)
        return result

    @functools.wraps(original_patch_thread)
    def patch_thread(*args: Any, **kwargs: Any) -> Any:
        result = original_patch_thread(*args, **kwargs)
        _enable_if_threading_patched(gevent_monkey)
        return result

    gevent_monkey.patch_all = patch_all
    gevent_monkey.patch_thread = patch_thread
    gevent_monkey._ddtrace_logging_wrapped = True
    _enable_if_threading_patched(gevent_monkey)
