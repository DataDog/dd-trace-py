"""Process-wide CPython GC pause observer.

One gc.callbacks subscriber. Install is refcounted via acquire/release.
Runtime metrics drain a snapshot on each flush.
"""

from __future__ import annotations

from enum import Enum
import gc
import time
from typing import Callable
from typing import NamedTuple
from typing import Optional

from ddtrace.internal import forksafe
from ddtrace.internal._unpatched import threading_Lock


GEN_COUNT: int = 3


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
        # Forksafe because _on_gc runs on whichever thread triggered the collection,
        # so a fork can inherit a lock held by a thread the child does not have.
        # Non-reentrant is sufficient because no critical section below allocates a
        # GC-tracked object while the callback is installed; see _gc_hook.
        self._lock: threading_Lock = forksafe.Lock()
        self._refcount: int = 0
        self._fork_registered: bool = False
        # Bind once. Evaluating self._on_gc builds a bound method, which is a
        # GC-tracked allocation, and acquire/release must not allocate.
        self._gc_hook: Callable[[str, dict[str, int]], None] = self._on_gc
        self._fork_hook: Callable[[], None] = self.reset
        self._start_ns: list[int] = [0] * GEN_COUNT
        self._count: int = 0
        self._total_ns: int = 0
        self._max_ns: int = 0

    def acquire(self) -> None:
        with self._lock:
            self._refcount += 1
            if self._refcount != 1:
                return

            # Register first: forksafe.register is a functools.partial, so calling
            # it builds an args tuple. Allocating after the callback is installed
            # could start a collection that reenters _on_gc on this thread.
            if not self._fork_registered:
                forksafe.register(self._fork_hook)
                self._fork_registered = True

            if self._gc_hook not in gc.callbacks:
                gc.callbacks.append(self._gc_hook)

    def release(self) -> None:
        with self._lock:
            if self._refcount <= 0:
                return

            self._refcount -= 1
            if self._refcount == 0:
                try:
                    gc.callbacks.remove(self._gc_hook)
                except ValueError:
                    pass

                if self._fork_registered:
                    forksafe.unregister(self._fork_hook)
                    self._fork_registered = False

                # Drop in-flight starts so a later re-acquire cannot pair a
                # new stop with a stale timestamp from before uninstall.
                self._clear_starts()
                self._clear_window()

    def reset(self) -> None:
        """Drop in-flight starts and the current window. Used after fork."""
        with self._lock:
            self._clear_starts()
            self._clear_window()

    def snapshot_and_reset(self) -> GCPauseSnapshot:
        # Copy primitives under the lock and build the tuple outside it. Allocating
        # can trigger a collection, and _on_gc would then run on this thread.
        with self._lock:
            n_pauses: int = self._count
            total_ns: int = self._total_ns
            max_ns: int = self._max_ns
            self._clear_window()
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

        if phase == _GCPhase.START:
            with self._lock:
                # A start callback can be waiting here while release() uninstalls.
                # Storing a timestamp then would leave it to pair with a stop after
                # the next acquire, reporting the gap between them as one pause.
                if self._refcount <= 0:
                    return

                self._start_ns[gen] = time.monotonic_ns()
            return

        if phase != _GCPhase.STOP:
            return

        with self._lock:
            start: int = self._start_ns[gen]
            if start == 0:
                return

            self._start_ns[gen] = 0
            pause_ns: int = time.monotonic_ns() - start
            if pause_ns < 0:
                return

            self._count += 1
            self._total_ns += pause_ns
            if pause_ns > self._max_ns:
                self._max_ns = pause_ns


_MONITOR: Optional[GCPauseMonitor] = None
_MONITOR_LOCK: threading_Lock = forksafe.Lock()


def gc_pause_monitor() -> GCPauseMonitor:
    """Process-wide monitor."""
    global _MONITOR
    with _MONITOR_LOCK:
        if _MONITOR is None:
            _MONITOR = GCPauseMonitor()
        return _MONITOR
