"""Process-wide CPython GC pause observer.

One gc.callbacks subscriber. Install is refcounted via acquire/release.
Runtime metrics drain a snapshot on each flush. Profiling can add_listener
for per-pause events without a second callback.
"""

from __future__ import annotations

from collections import deque
from contextlib import contextmanager
from enum import Enum
import gc
import sys
import time
from typing import Any
from typing import Callable
from typing import Iterator
from typing import NamedTuple
from typing import Optional
import weakref

from ddtrace.internal import forksafe
from ddtrace.internal._unpatched import threading_Lock
from ddtrace.internal.logger import get_logger


log = get_logger(__name__)

GEN_COUNT: int = 3

# Every section that holds the lock is short and never blocks. A lock held this long
# was most likely left held by a signal handler that raised between _on_gc's acquire()
# and its try, which Python code cannot prevent. Sections that would wait give up
# instead of hanging the thread that starts the profiler or flushes runtime metrics.
# os.fork() joins ddtrace's threads first, so a hung one would hang the fork too.
_HELD_LOCK_TIMEOUT_S: float = 1.0
_HELD_LOCK_POLL_S: float = 0.001


class GCMonitorLockHeld(RuntimeError):
    """The monitor lock stayed held for longer than any section holds it."""


# A pause is the CPU time of the thread that collects. Python code that runs during
# a collection (gc.callbacks, this one included, and finalizers) can hand the GIL to
# other threads, and wall time would count all the time they run as pause.
# Windows thread time only advances once per scheduler tick, so keep wall time there.
# macOS builds before thread_time_ns existed (and any other build without it) also
# fall back to wall time — getattr, not a platform name check, so a newer macOS
# Python that exposes the clock still uses it.
_thread_time_ns: Callable[[], int] | None = getattr(time, "thread_time_ns", None)
_pause_clock_ns: Callable[[], int] = (
    time.monotonic_ns if sys.platform == "win32" or _thread_time_ns is None else _thread_time_ns
)

# (generation, pause_ns)
PauseListener = Callable[[int, int], None]


def _gc_callbacks_supported() -> bool:
    return hasattr(gc, "callbacks")


def _gc_callback_installed(hook: Callable[[str, dict[str, int]], None]) -> bool:
    callbacks: list[Any] = gc.callbacks
    i: int
    for i in range(len(callbacks)):
        if callbacks[i] is hook:
            return True
    return False


def _remove_gc_callback(hook: Callable[[str, dict[str, int]], None]) -> None:
    callbacks: list[Any] = gc.callbacks
    i: int = 0
    while i < len(callbacks):
        if callbacks[i] is hook:
            callbacks.pop(i)
        else:
            i += 1


class _GCPhase(str, Enum):
    # CPython gc.callbacks phase is only these two strings (docs.python.org/3/library/gc.html).
    START = "start"
    STOP = "stop"


class GCPauseSnapshot(NamedTuple):
    n_pauses: int
    total_ns: int
    max_ns: int


class GCPauseMonitor:
    """Single gc.callbacks subscriber with refcounted install."""

    _lock: threading_Lock
    _refcount: int
    _fork_registered: bool
    _gc_hook: Callable[[str, dict[str, int]], None]
    _fork_hook: Callable[[], None]
    _start_ns: list[int]
    _count: int
    _total_ns: int
    _max_ns: int

    def __init__(self) -> None:
        # _on_gc must never wait for this lock. CPython can start a collection on a
        # thread that holds the lock, and _on_gc would then wait for its own thread. That
        # collection would never finish, and CPython would start no other collection.
        # A plain lock, not forksafe.Lock(): _on_gc takes it on every collection, and
        # the forksafe proxy makes each acquire and release many times slower. A fork
        # can still inherit it held by a thread that the child does not have, so
        # _reset_monitor_locks resets it in place in the child.
        # Non-reentrant on purpose: same-thread reentry from _on_gc while snapshot
        # (or acquire) holds the lock must fail the nonblocking acquire and skip,
        # not nest. RLock would let _on_gc mutate under an outer critical section.
        self._lock: threading_Lock = threading_Lock()
        _monitors_with_plain_locks.add(self)
        # Stop work (remove_listener, release) for whichever thread holds the lock.
        # Stop never waits for the lock: a profiler SIGTERM/SIGINT handler runs on the
        # main thread between bytecodes, and can land while that same thread holds this
        # non-reentrant lock, including right after acquire() or right before release().
        self._pending_removes: deque[PauseListener] = deque()
        self._pending_releases: deque[None] = deque()
        self._refcount: int = 0
        self._fork_registered: bool = False
        self._held_lock_logged: bool = False
        # Set when the last release is applied inside _on_gc. CPython walks
        # gc.callbacks by index while it calls them, so removing this hook there
        # would skip the callback after it for that phase. The hook stays installed,
        # inert at refcount 0, until a section outside a collection removes it.
        self._hook_removal_pending: bool = False
        # Bind once. Each evaluation of self._on_gc builds a new bound method, and
        # _remove_gc_callback finds the installed hook by identity.
        self._gc_hook: Callable[[str, dict[str, int]], None] = self._on_gc
        self._fork_hook: Callable[[], None] = self.reset
        self._start_ns: list[int] = [0] * GEN_COUNT
        self._count: int = 0
        self._total_ns: int = 0
        self._max_ns: int = 0
        # Tuple so _on_gc can iterate without allocating a copy.
        self._listeners: tuple[PauseListener, ...] = ()

    def _has_pending(self, in_gc_callback: bool) -> bool:
        return bool(
            self._pending_removes or self._pending_releases or (self._hook_removal_pending and not in_gc_callback)
        )

    def _apply_pending(self, in_gc_callback: bool = False) -> None:
        # Stop queues its work before it tries the lock, and every unlock checks the
        # queues after it releases, so whichever thread comes last applies the work.
        # A failed entry is logged, not raised: the caller can be a GC callback (where
        # CPython prints it as unraisable) or a stop that must still finish.
        while self._has_pending(in_gc_callback):
            if not self._lock.acquire(False):
                return
            failed: bool = False
            try:
                while self._pending_removes:
                    listener: PauseListener = self._pending_removes[0]
                    try:
                        self._apply_remove_listener_locked(listener)
                    except Exception:
                        # Removing is idempotent, so keep it queued for the next unlock.
                        log.debug("Failed to remove GC pause listener %r", listener, exc_info=True)
                        failed = True
                        break
                    self._pending_removes.popleft()
                while self._pending_releases:
                    # Popped first: a release that raised after the refcount drop must
                    # not drop it again on retry.
                    self._pending_releases.popleft()
                    try:
                        self._apply_one_release_locked(in_gc_callback)
                    except Exception:
                        log.debug("Failed to release the GC pause monitor", exc_info=True)
                if self._hook_removal_pending and not in_gc_callback:
                    self._hook_removal_pending = False
                    if self._refcount == 0 and _gc_callbacks_supported():
                        _remove_gc_callback(self._gc_hook)
            finally:
                self._lock.release()
            if failed:
                return

    def _wait_while_held(self) -> None:
        if not self._lock.locked():
            return
        deadline: float = time.monotonic() + _HELD_LOCK_TIMEOUT_S
        # An unpatched lock's timed acquire as the sleep, so this waits the same way
        # under gevent, on application threads and on ddtrace threads.
        pause: threading_Lock = threading_Lock()
        pause.acquire()
        while self._lock.locked():
            if time.monotonic() >= deadline:
                raise GCMonitorLockHeld("GC pause monitor lock held for over %ss" % _HELD_LOCK_TIMEOUT_S)
            pause.acquire(timeout=_HELD_LOCK_POLL_S)

    @contextmanager
    def _holding_lock(self) -> Iterator[None]:
        # Take the lock with a with statement, not acquire() then try. Except on 3.14,
        # CPython runs no signal handler between a with statement taking the lock and
        # its protection starting, so a raising handler cannot leave the lock held.
        # A timed acquire would reopen that gap, so wait for the lock to be free
        # first, and raise GCMonitorLockHeld rather than block forever on a lock that
        # _on_gc left held. A holder that takes it between that wait and the with
        # statement can still make the with statement wait.
        # Then apply stop work queued while it was held, as _on_gc does.
        self._wait_while_held()
        try:
            with self._lock:
                yield
        finally:
            if self._has_pending(False):
                self._apply_pending()

    def _apply_remove_listener_locked(self, listener: PauseListener) -> None:
        self._listeners = tuple(item for item in self._listeners if item is not listener)

    def _apply_one_release_locked(self, in_gc_callback: bool = False) -> None:
        """Drop one refcount under the lock, and uninstall when it reaches zero."""
        if self._refcount <= 0:
            return

        self._refcount -= 1
        if self._refcount != 0:
            return

        if in_gc_callback:
            self._hook_removal_pending = True
        elif _gc_callbacks_supported():
            _remove_gc_callback(self._gc_hook)
        if self._fork_registered:
            forksafe.unregister(self._fork_hook)
            self._fork_registered = False
        # Drop in-flight starts so a later re-acquire cannot pair a
        # new stop with a stale timestamp from before uninstall.
        self._clear_starts()
        self._clear_window()

    def add_listener(self, listener: PauseListener) -> None:
        with self._holding_lock():
            self._listeners = self._listeners + (listener,)

    def remove_listener(self, listener: PauseListener) -> None:
        # Matched by identity, so pass the object that add_listener got (a cached bound
        # method, not a fresh obj.method): == could run listener code under the lock,
        # inside a GC callback.
        # Applied now if the lock is free, otherwise by the holder when it unlocks.
        self._pending_removes.append(listener)
        self._apply_pending()

    def acquire(self) -> None:
        # Install in the same critical section as the increment, as
        # _apply_one_release_locked uninstalls in the one that drops the refcount to
        # zero. A stop that lands in here is applied when this unlocks, so it removes
        # what this installed, and an install and an uninstall cannot interleave.
        with self._holding_lock():
            self._refcount += 1
            if self._refcount != 1:
                return
            if not _gc_callbacks_supported():
                return

            if not self._fork_registered:
                forksafe.register(self._fork_hook)
                self._fork_registered = True
            if not _gc_callback_installed(self._gc_hook):
                gc.callbacks.append(self._gc_hook)

    def release(self) -> None:
        # Same as remove_listener: never wait for the lock.
        self._pending_releases.append(None)
        self._apply_pending()

    def reset(self) -> None:
        """Drop in-flight starts and the current window. Used after fork."""
        try:
            with self._holding_lock():
                self._clear_starts()
                self._clear_window()
        except GCMonitorLockHeld:
            log.debug("GC pause monitor lock left held, not resetting", exc_info=True)

    def snapshot_and_reset(self) -> GCPauseSnapshot:
        # Copy primitives under the lock and build the tuple outside it. Allocating
        # can trigger a collection, and _on_gc would then run on this thread.
        try:
            with self._holding_lock():
                n_pauses: int = self._count
                total_ns: int = self._total_ns
                max_ns: int = self._max_ns
                self._clear_window()
        except GCMonitorLockHeld:
            # Skip this interval. The window keeps its pauses for a later snapshot.
            if not self._held_lock_logged:
                self._held_lock_logged = True
                log.debug("GC pause monitor lock left held, reporting no pauses until it is free")
            return GCPauseSnapshot(0, 0, 0)
        return GCPauseSnapshot(n_pauses, total_ns, max_ns)

    def _clear_starts(self) -> None:
        # In place: rebinding to a fresh list would allocate while holding the lock.
        for gen in range(GEN_COUNT):
            self._start_ns[gen] = 0

    def _clear_window(self) -> None:
        self._count = 0
        self._total_ns = 0
        self._max_ns = 0

    def _on_gc(self, phase: str, info: dict[str, int]) -> None:
        # Do not allocate: object creation in a GC callback can recurse.
        gen: int = info.get("generation", 0)
        if not 0 <= gen < GEN_COUNT:
            return

        # Skip the sample when another section holds the lock. Waiting could block
        # forever, see __init__. Also drop the start of this generation, so that a
        # later stop cannot pair with it. Writing zero without the lock is safe
        # because every other writer holds the lock and only writes zero, or is
        # _on_gc, and CPython runs one collection at a time.
        pause_ns: int = 0
        listeners: tuple[PauseListener, ...] = ()
        if not self._lock.acquire(False):
            self._start_ns[gen] = 0
            return

        # DEV: CPython runs pending signal handlers when a call returns. If a handler
        # raises between acquire() and try, the lock stays held preventing us
        # from sending any further metrics: snapshot_and_reset() reports no pauses,
        # acquire() and add_listener() raise GCMonitorLockHeld after a bounded wait,
        # and stop work queued by remove_listener() and release() is never applied.
        # A with statement cannot acquire without blocking, so Python code
        # cannot close this gap.
        try:
            if phase == _GCPhase.START:
                # A start callback can run after release() uninstalls. Storing a
                # timestamp then would leave it to pair with a stop after the next
                # acquire, reporting the gap between them as one pause.
                if self._refcount <= 0:
                    return

                self._start_ns[gen] = _pause_clock_ns()
                return

            if phase != _GCPhase.STOP:
                return

            start: int = self._start_ns[gen]
            if start == 0:
                return

            self._start_ns[gen] = 0
            pause_ns = _pause_clock_ns() - start
            if pause_ns < 0:
                return

            self._count += 1
            self._total_ns += pause_ns
            if pause_ns > self._max_ns:
                self._max_ns = pause_ns
            listeners = self._listeners
        finally:
            # Released here, not in a helper: a signal handler can run when a Python
            # function starts, before it would release.
            self._lock.release()
            # Also applies a stop that a signal handler queued while this callback
            # held the lock.
            if self._pending_removes or self._pending_releases:
                self._apply_pending(in_gc_callback=True)

        # Call after the lock is dropped. The tuple was swapped in add/remove,
        # so this loop does not allocate.
        listener: PauseListener
        for listener in listeners:
            try:
                listener(gen, pause_ns)
            except Exception:
                # This can also be a signal handler's exception (a SIGALRM timeout,
                # say). CPython drops whatever a GC callback raises, so log it rather
                # than lose it silently.
                log.debug("GC pause listener %r raised", listener, exc_info=True)


_monitors_with_plain_locks: weakref.WeakSet[GCPauseMonitor] = weakref.WeakSet()


@forksafe.register
def _reset_monitor_locks() -> Iterator[None]:
    # A wrapper, so the reset runs before every regular after-fork hook in the child,
    # whatever the import order, including reset(), which takes the lock.
    for monitor in list(_monitors_with_plain_locks):
        monitor._lock._at_fork_reinit()  # type: ignore[attr-defined]
    yield


_MONITOR: Optional[GCPauseMonitor] = None
_MONITOR_LOCK: threading_Lock = forksafe.Lock()


def gc_pause_monitor() -> GCPauseMonitor:
    """Process-wide monitor."""
    global _MONITOR
    with _MONITOR_LOCK:
        if _MONITOR is None:
            _MONITOR = GCPauseMonitor()
        return _MONITOR
