"""Process-wide CPython GC pause observer.

One ``gc.callbacks`` subscriber. Runtime metrics drain ``snapshot_and_reset``
on each flush. Profiling can ``add_listener`` for per-pause events without a
second callback.
"""

from __future__ import annotations

import gc
import sys
import threading
import time
from typing import Any
from typing import Callable
from typing import NamedTuple
from typing import Optional

from ddtrace.internal import forksafe
from ddtrace.internal.logger import get_logger


log = get_logger(__name__)

GEN_COUNT = 3

# (generation, pause_ns, start_ns, triggering frame or None)
PauseListener = Callable[[int, int, int, Any], None]


class _GenWindow:
    __slots__ = ("count", "total_ns", "max_ns")

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
        empty = (0, 0, 0)
        return cls(0, 0, 0, (empty, empty, empty))


class GCPauseMonitor:
    """Single ``gc.callbacks`` subscriber with refcounted install."""

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
        self._listeners: list[PauseListener] = []

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
                self._clear_window()

    def add_listener(self, listener: PauseListener) -> None:
        with self._lock:
            self._listeners.append(listener)

    def remove_listener(self, listener: PauseListener) -> None:
        with self._lock:
            try:
                self._listeners.remove(listener)
            except ValueError:
                pass

    def reset(self) -> None:
        """Drop in-flight starts and the current window. Used after fork."""
        with self._lock:
            self._start_ns = [0] * GEN_COUNT
            self._clear_window()

    def snapshot_and_reset(self) -> GCPauseSnapshot:
        with self._lock:
            per_gen = tuple((w.count, w.total_ns, w.max_ns) for w in self._per_gen)
            snap = GCPauseSnapshot(self._count, self._total_ns, self._max_ns, per_gen)
            self._clear_window()
            return snap

    def _clear_window(self) -> None:
        self._count = 0
        self._total_ns = 0
        self._max_ns = 0
        for w in self._per_gen:
            w.count = 0
            w.total_ns = 0
            w.max_ns = 0

    def _on_gc(self, phase: str, info: dict[str, int]) -> None:
        # Do not allocate on the metrics-only path: object creation in a GC
        # callback can recurse. Listeners are a profiling opt-in.
        try:
            gen = info.get("generation", 0)
            if not 0 <= gen < GEN_COUNT:
                return
            if phase == "start":
                with self._lock:
                    self._start_ns[gen] = time.monotonic_ns()
                return
            if phase != "stop":
                return
            with self._lock:
                start = self._start_ns[gen]
                if start == 0:
                    return
                self._start_ns[gen] = 0
                pause_ns = time.monotonic_ns() - start
                if pause_ns < 0:
                    return
                window = self._per_gen[gen]
                window.count += 1
                window.total_ns += pause_ns
                if pause_ns > window.max_ns:
                    window.max_ns = pause_ns
                self._count += 1
                self._total_ns += pause_ns
                if pause_ns > self._max_ns:
                    self._max_ns = pause_ns
                listeners: Optional[tuple[PauseListener, ...]] = tuple(self._listeners) if self._listeners else None
            if not listeners:
                return
            try:
                frame = sys._getframe(1)
            except ValueError:
                frame = None
            for listener in listeners:
                try:
                    listener(gen, pause_ns, start, frame)
                except Exception:
                    log.debug("GC pause listener failed", exc_info=True)
        except Exception:
            log.debug("GC pause monitor callback failed", exc_info=True)


_MONITOR: Optional[GCPauseMonitor] = None
_MONITOR_LOCK = threading.Lock()


def gc_pause_monitor() -> GCPauseMonitor:
    """Process-wide monitor. Metrics and profiling share this instance."""
    global _MONITOR
    with _MONITOR_LOCK:
        if _MONITOR is None:
            _MONITOR = GCPauseMonitor()
        return _MONITOR
