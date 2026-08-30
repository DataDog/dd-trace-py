"""Process-wide CPython GC pause observer.

One gc.callbacks subscriber. Install is refcounted via acquire/release.
Runtime metrics drain a snapshot on each flush.
"""

from __future__ import annotations

import gc
import logging
import threading
import time
from typing import NamedTuple
from typing import Optional

from ddtrace.internal import forksafe
from ddtrace.internal._unpatched import threading_Lock
from ddtrace.internal.logger import get_logger


log: logging.Logger = get_logger(__name__)

GEN_COUNT: int = 3


class _GenWindow:
    __slots__ = ("count", "total_ns", "max_ns")
    count: int
    total_ns: int
    max_ns: int

    def __init__(self) -> None:
        self.count = 0
        self.total_ns = 0
        self.max_ns = 0


class GCPauseSnapshot(NamedTuple):
    n_pauses: int
    total_ns: int
    max_ns: int
    # (n_pauses, total_ns, max_ns) per generation
    per_gen: tuple[tuple[int, int, int], ...]

    @classmethod
    def zeros(cls) -> GCPauseSnapshot:
        empty: tuple[int, int, int] = (0, 0, 0)
        return cls(0, 0, 0, (empty, empty, empty))


class GCPauseMonitor:
    """Single gc.callbacks subscriber with refcounted install."""

    _lock: threading.RLock
    _refcount: int
    _fork_registered: bool
    _start_ns: list[int]
    _count: int
    _total_ns: int
    _max_ns: int
    _per_gen: list[_GenWindow]

    def __init__(self) -> None:
        # RLock: snapshot_and_reset allocates, which can reenter GC in this thread.
        self._lock = threading.RLock()
        self._refcount = 0
        self._fork_registered = False
        self._start_ns = [0] * GEN_COUNT
        self._count = 0
        self._total_ns = 0
        self._max_ns = 0
        self._per_gen = [_GenWindow() for _ in range(GEN_COUNT)]

    def acquire(self) -> None:
        with self._lock:
            self._refcount += 1
            if self._refcount == 1:
                if self._on_gc not in gc.callbacks:
                    gc.callbacks.append(self._on_gc)
                if not self._fork_registered:
                    forksafe.register(self.reset)
                    self._fork_registered = True

    def release(self) -> None:
        with self._lock:
            if self._refcount <= 0:
                return
            self._refcount -= 1
            if self._refcount == 0:
                try:
                    gc.callbacks.remove(self._on_gc)
                except ValueError:
                    pass
                # Drop in-flight starts so a later re-acquire cannot pair a
                # new stop with a stale timestamp from before uninstall.
                self._start_ns = [0] * GEN_COUNT
                self._clear_window()

    def reset(self) -> None:
        """Drop in-flight starts and the current window. Used after fork."""
        with self._lock:
            self._start_ns = [0] * GEN_COUNT
            self._clear_window()

    def snapshot_and_reset(self) -> GCPauseSnapshot:
        # Copy primitives, then clear, then allocate. A reentrant GC callback
        # during NamedTuple/tuple construction must land in the next window.
        with self._lock:
            n_pauses: int = self._count
            total_ns: int = self._total_ns
            max_ns: int = self._max_ns
            g0: _GenWindow = self._per_gen[0]
            g1: _GenWindow = self._per_gen[1]
            g2: _GenWindow = self._per_gen[2]
            c0: int = g0.count
            t0: int = g0.total_ns
            m0: int = g0.max_ns
            c1: int = g1.count
            t1: int = g1.total_ns
            m1: int = g1.max_ns
            c2: int = g2.count
            t2: int = g2.total_ns
            m2: int = g2.max_ns
            self._clear_window()
        per_gen: tuple[tuple[int, int, int], ...] = ((c0, t0, m0), (c1, t1, m1), (c2, t2, m2))
        return GCPauseSnapshot(n_pauses, total_ns, max_ns, per_gen)

    def _clear_window(self) -> None:
        self._count = 0
        self._total_ns = 0
        self._max_ns = 0
        for w in self._per_gen:
            w.count = 0
            w.total_ns = 0
            w.max_ns = 0

    def _on_gc(self, phase: str, info: dict[str, int]) -> None:
        # Do not allocate: object creation in a GC callback can recurse.
        try:
            gen: int = info.get("generation", 0)
            if not 0 <= gen < GEN_COUNT:
                return
            if phase == "start":
                with self._lock:
                    self._start_ns[gen] = time.monotonic_ns()
                return
            if phase != "stop":
                return
            with self._lock:
                start: int = self._start_ns[gen]
                if start == 0:
                    return
                self._start_ns[gen] = 0
                pause_ns: int = time.monotonic_ns() - start
                if pause_ns < 0:
                    return
                window: _GenWindow = self._per_gen[gen]
                window.count += 1
                window.total_ns += pause_ns
                if pause_ns > window.max_ns:
                    window.max_ns = pause_ns
                self._count += 1
                self._total_ns += pause_ns
                if pause_ns > self._max_ns:
                    self._max_ns = pause_ns
        except Exception:
            log.debug("GC pause monitor callback failed", exc_info=True)


_MONITOR: Optional[GCPauseMonitor] = None
_MONITOR_LOCK: threading_Lock = forksafe.Lock()


def gc_pause_monitor() -> GCPauseMonitor:
    """Process-wide monitor."""
    global _MONITOR
    with _MONITOR_LOCK:
        if _MONITOR is None:
            _MONITOR = GCPauseMonitor()
        return _MONITOR
