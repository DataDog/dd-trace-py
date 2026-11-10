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

from ddtrace.internal import forksafe
from ddtrace.internal._unpatched import threading_Lock
from ddtrace.internal.threads import original_local


GEN_COUNT: int = 3

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


class _UnlockWork(NamedTuple):
    """Callback uninstall / fork-hook work that must run outside the lock."""

    remove_callback: bool
    unregister_fork: bool


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
        # Forksafe because a fork can inherit the lock from a thread that the child
        # does not have.
        # Non-reentrant on purpose: same-thread reentry from _on_gc while snapshot
        # (or acquire) holds the lock must fail the nonblocking acquire and skip,
        # not nest. RLock would let _on_gc mutate under an outer critical section.
        self._lock: threading_Lock = forksafe.Lock()
        # Depth of lock ownership on this thread. threading.Lock has no owner API;
        # we track it so signal-triggered stop can defer instead of deadlocking when
        # it reenters remove_listener/release while _on_gc (or snapshot) holds the lock.
        self._lock_owner: Any = original_local()
        self._deferred_removes: deque[PauseListener] = deque()
        self._deferred_releases: int = 0
        self._refcount: int = 0
        self._fork_registered: bool = False
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

    def _lock_owned(self) -> bool:
        return getattr(self._lock_owner, "depth", 0) > 0

    def _lock_acquire(self, blocking: bool = True) -> bool:
        acquired: bool
        if blocking:
            acquired = self._lock.acquire()
        else:
            acquired = self._lock.acquire(False)
        if acquired:
            owner: Any = self._lock_owner
            owner.depth = getattr(owner, "depth", 0) + 1
        return acquired

    def _lock_release(self) -> None:
        owner: Any = self._lock_owner
        owner.depth = getattr(owner, "depth", 0) - 1
        self._lock.release()

    @contextmanager
    def _holding_lock(self) -> Iterator[None]:
        self._lock_acquire(True)
        try:
            yield
        finally:
            self._lock_release()

    def _apply_remove_listener_locked(self, listener: PauseListener) -> None:
        self._listeners = tuple(item for item in self._listeners if item is not listener)

    def _apply_one_release_locked(self) -> _UnlockWork:
        """Drop one refcount under the lock. Caller runs unlock work outside."""
        if self._refcount <= 0:
            return _UnlockWork(False, False)

        self._refcount -= 1
        if self._refcount != 0:
            return _UnlockWork(False, False)

        unregister_fork: bool = self._fork_registered
        # Drop in-flight starts so a later re-acquire cannot pair a
        # new stop with a stale timestamp from before uninstall.
        self._clear_starts()
        self._clear_window()
        return _UnlockWork(True, unregister_fork)

    def _drain_deferred_locked(self) -> list[_UnlockWork]:
        """Apply stop work deferred while this thread already held the lock."""
        work: list[_UnlockWork] = []
        while self._deferred_removes or self._deferred_releases > 0:
            while self._deferred_removes:
                listener: PauseListener = self._deferred_removes.popleft()
                self._apply_remove_listener_locked(listener)
            while self._deferred_releases > 0:
                self._deferred_releases -= 1
                work.append(self._apply_one_release_locked())
        return work

    def _run_unlock_work(self, work_items: list[_UnlockWork]) -> None:
        item: _UnlockWork
        for item in work_items:
            if item.remove_callback and _gc_callbacks_supported():
                _remove_gc_callback(self._gc_hook)
            if item.unregister_fork:
                forksafe.unregister(self._fork_hook)
                with self._holding_lock():
                    self._fork_registered = False

    def _unlock_and_run(self, work_items: list[_UnlockWork]) -> None:
        self._lock_release()
        if work_items:
            self._run_unlock_work(work_items)

    def add_listener(self, listener: PauseListener) -> None:
        with self._holding_lock():
            self._listeners = self._listeners + (listener,)

    def remove_listener(self, listener: PauseListener) -> None:
        # Profiler SIGTERM/SIGINT stop can run on the main thread between bytecodes
        # while _on_gc or snapshot_and_reset already holds this lock. Waiting would
        # deadlock: the lock is non-reentrant and the same thread owns it. Queue the
        # removal; the holder drains it before unlocking (_on_gc finally, or the next
        # remove_listener/release that gets the lock).
        if self._lock_owned():
            self._deferred_removes.append(listener)
            return

        work: list[_UnlockWork] = []
        self._lock_acquire(True)
        try:
            self._apply_remove_listener_locked(listener)
            work = self._drain_deferred_locked()
        finally:
            self._unlock_and_run(work)

    def acquire(self) -> None:
        with self._holding_lock():
            self._refcount += 1
            if self._refcount != 1:
                return
            if not _gc_callbacks_supported():
                return

        with self._holding_lock():
            fork_registered: bool = self._fork_registered
        if not fork_registered:
            forksafe.register(self._fork_hook)
            with self._holding_lock():
                self._fork_registered = True

        if not _gc_callback_installed(self._gc_hook):
            gc.callbacks.append(self._gc_hook)

    def release(self) -> None:
        # Same signal-stop reentry as remove_listener: never block on a lock this
        # thread already holds.
        if self._lock_owned():
            self._deferred_releases += 1
            return

        work: list[_UnlockWork] = []
        self._lock_acquire(True)
        try:
            work = self._drain_deferred_locked()
            work.append(self._apply_one_release_locked())
        finally:
            self._unlock_and_run(work)

    def reset(self) -> None:
        """Drop in-flight starts and the current window. Used after fork."""
        with self._holding_lock():
            self._clear_starts()
            self._clear_window()

    def snapshot_and_reset(self) -> GCPauseSnapshot:
        # Copy primitives under the lock and build the tuple outside it. Allocating
        # can trigger a collection, and _on_gc would then run on this thread.
        # Drain deferred stop work here too: a signal can interrupt the main thread
        # during this section the same way it can during _on_gc.
        work: list[_UnlockWork] = []
        self._lock_acquire(True)
        try:
            n_pauses: int = self._count
            total_ns: int = self._total_ns
            max_ns: int = self._max_ns
            self._clear_window()
            work = self._drain_deferred_locked()
        finally:
            self._unlock_and_run(work)
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
        if not self._lock_acquire(False):
            self._start_ns[gen] = 0
            return

        pause_ns: int = 0
        listeners: tuple[PauseListener, ...] = ()
        # DEV: CPython runs pending signal handlers when a call returns. If a handler
        # raises between acquire() and try, the lock stays held preventing us
        # from sending any further metrics, and snapshot_and_reset() and release()
        # will block forever.
        # A with statement cannot acquire without blocking, so Python code
        # cannot closethis gap.
        work: list[_UnlockWork] = []
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
            # Drain stop work deferred by a signal handler that interrupted this
            # callback, then drop the lock. Deferred release must run while we still
            # own the lock; otherwise signal stop would leave the callback installed
            # until some later remove_listener/release.
            work = self._drain_deferred_locked()
            self._unlock_and_run(work)

        # Call after the lock is dropped. The tuple was swapped in add/remove,
        # so this loop does not allocate.
        listener: PauseListener
        for listener in listeners:
            try:
                listener(gen, pause_ns)
            except Exception:  # nosec B110
                pass


_MONITOR: Optional[GCPauseMonitor] = None
_MONITOR_LOCK: threading_Lock = forksafe.Lock()


def gc_pause_monitor() -> GCPauseMonitor:
    """Process-wide monitor."""
    global _MONITOR
    with _MONITOR_LOCK:
        if _MONITOR is None:
            _MONITOR = GCPauseMonitor()
        return _MONITOR
