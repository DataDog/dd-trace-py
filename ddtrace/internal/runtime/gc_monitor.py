"""Process-wide CPython GC pause observer.

One gc.callbacks subscriber. Install is refcounted via acquire/release.
Runtime metrics drain a snapshot on each flush.
"""

from __future__ import annotations

from enum import Enum
import gc
import time
from typing import Any
from typing import Callable
from typing import NamedTuple
from typing import Optional

from ddtrace.internal import forksafe
from ddtrace.internal._unpatched import threading_Lock


GEN_COUNT: int = 3


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
        # _on_gc must never take this lock. CPython can start a collection on a thread
        # that holds the lock, and _on_gc would then wait for its own thread. That
        # collection would never finish, and CPython would start no other collection.
        # Forksafe because a fork can inherit the lock from a thread that the child
        # does not have.
        self._lock: threading_Lock = forksafe.Lock()
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

    def acquire(self) -> None:
        with self._lock:
            self._refcount += 1
            if self._refcount != 1:
                return
            if not _gc_callbacks_supported():
                return

        with self._lock:
            fork_registered: bool = self._fork_registered
        if not fork_registered:
            forksafe.register(self._fork_hook)
            with self._lock:
                self._fork_registered = True

        if not _gc_callback_installed(self._gc_hook):
            gc.callbacks.append(self._gc_hook)

    def release(self) -> None:
        fork_registered: bool = False
        with self._lock:
            if self._refcount <= 0:
                return

            self._refcount -= 1
            if self._refcount != 0:
                return

            fork_registered = self._fork_registered
            # Drop in-flight starts so a later re-acquire cannot pair a
            # new stop with a stale timestamp from before uninstall.
            self._clear_starts()
            self._clear_window()

        if _gc_callbacks_supported():
            _remove_gc_callback(self._gc_hook)

        if fork_registered:
            forksafe.unregister(self._fork_hook)
            with self._lock:
                self._fork_registered = False

    def reset(self) -> None:
        """Drop in-flight starts and the current window. Used after fork."""
        with self._lock:
            self._clear_starts()
            self._clear_window()

    def snapshot_and_reset(self) -> GCPauseSnapshot:
        with self._lock:
            # _on_gc writes these fields without the lock. Under the GIL, CPython
            # switches threads and runs a pending collection only at an eval-breaker
            # check or inside a call. With no call between the reads and the writes,
            # no pause can land between them and get lost.
            n_pauses: int = self._count
            total_ns: int = self._total_ns
            max_ns: int = self._max_ns
            self._count = 0
            self._total_ns = 0
            self._max_ns = 0
        return GCPauseSnapshot(n_pauses, total_ns, max_ns)

    def _clear_starts(self) -> None:
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
            # A start callback can run after release() uninstalls. Storing a timestamp
            # then would leave it to pair with a stop after the next acquire, reporting
            # the gap between them as one pause. Read the clock first, so that no call
            # separates the refcount check from the write, as in snapshot_and_reset.
            now_ns: int = time.monotonic_ns()
            if self._refcount > 0:
                self._start_ns[gen] = now_ns
            return

        if phase != _GCPhase.STOP:
            return

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
