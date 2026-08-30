from __future__ import annotations

import gc
import logging
import threading
import time
from typing import Callable

from ddtrace.internal import forksafe
from ddtrace.internal.datadog.profiling import ddup
from ddtrace.profiling import collector


LOG = logging.getLogger(__name__)

_GEN_NAMES: tuple[str, ...] = (
    "gc.collect[gen=0]",
    "gc.collect[gen=1]",
    "gc.collect[gen=2]",
)
_GEN_COUNT: int = len(_GEN_NAMES)
_GC_CONFIG_FRAME: str = "gc.config"
_GC_FILE: str = "gc"


class GCCollector(collector.Collector):
    """Collect CPython GC pause durations and explicit gc.collect() call counts.

    Hooks gc.callbacks for per-collection events and emits a snapshot sample
    once per profile flush interval.

    Data emitted:
    - Wall time samples (push_walltime) attributed to synthetic gc.collect[gen=N]
      frames. These appear in the Wall Time profile view.
    - A gc.config snapshot sample per flush carrying the interval's explicit
      gc.collect() tally in the sample count field.

    Thresholds, freeze count, and cumulative collection totals are DEBUG-only.
    """

    _lock: threading.Lock
    _start_ns: list[int]
    _pause_count: list[int]
    _pause_total_ns: list[int]
    _explicit_count: int
    _orig_collect: Callable[..., int]
    _installed_collect: Callable[..., int]

    def _start_service(self) -> None:
        self._lock = threading.Lock()
        self._start_ns = [0] * _GEN_COUNT
        self._pause_count = [0] * _GEN_COUNT
        self._pause_total_ns = [0] * _GEN_COUNT
        self._explicit_count = 0
        self._orig_collect = gc.collect
        # Bound-method lookups allocate a new object each time; keep the
        # installed callable so teardown can identity-check it.
        self._installed_collect = self._patched_collect
        gc.collect = self._installed_collect
        gc.callbacks.append(self._on_gc)
        forksafe.register(self._reset_after_fork)
        LOG.debug("GCCollector started")

    def _stop_service(self) -> None:
        try:
            gc.callbacks.remove(self._on_gc)
        except ValueError:
            pass
        forksafe.unregister(self._reset_after_fork)
        if gc.collect is self._installed_collect:
            gc.collect = self._orig_collect
        LOG.debug("GCCollector stopped")

    def _reset_after_fork(self) -> None:
        with self._lock:
            self._clear_window()

    def _clear_window(self) -> None:
        # Caller holds _lock. In-place zeros so a reentrant GC callback cannot
        # pair a new stop with a stale start from before the reset.
        for i in range(_GEN_COUNT):
            self._start_ns[i] = 0
            self._pause_count[i] = 0
            self._pause_total_ns[i] = 0
        self._explicit_count = 0

    def _patched_collect(self, generation: int = 2) -> int:
        with self._lock:
            self._explicit_count += 1
        return self._orig_collect(generation)

    def _on_gc(self, phase: str, info: dict[str, int]) -> None:
        # Do not allocate: object creation in a GC callback can recurse.
        # A nested same-gen start would overwrite the in-flight timestamp and
        # drop the outer pause.
        gen: int = info.get("generation", 0)
        if not 0 <= gen < _GEN_COUNT:
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
            self._pause_count[gen] += 1
            self._pause_total_ns[gen] += pause_ns

    def snapshot(self) -> None:  # type: ignore[override]
        # Copy primitives, then clear, then allocate. A reentrant GC callback
        # during SampleHandle construction must land in the next window.
        with self._lock:
            c0: int = self._pause_count[0]
            c1: int = self._pause_count[1]
            c2: int = self._pause_count[2]
            t0: int = self._pause_total_ns[0]
            t1: int = self._pause_total_ns[1]
            t2: int = self._pause_total_ns[2]
            explicit: int = self._explicit_count
            self._pause_count[0] = 0
            self._pause_count[1] = 0
            self._pause_count[2] = 0
            self._pause_total_ns[0] = 0
            self._pause_total_ns[1] = 0
            self._pause_total_ns[2] = 0
            self._explicit_count = 0

        pause_count: tuple[int, int, int] = (c0, c1, c2)
        pause_total_ns: tuple[int, int, int] = (t0, t1, t2)
        for gen in range(_GEN_COUNT):
            n: int = pause_count[gen]
            if n == 0:
                continue
            pause_handle: ddup.SampleHandle = ddup.SampleHandle()
            pause_handle.push_walltime(pause_total_ns[gen], n)
            pause_handle.push_frame(_GEN_NAMES[gen], _GC_FILE, 0, gen)
            pause_handle.push_monotonic_ns(time.monotonic_ns())
            pause_handle.flush_sample()

        thresholds: tuple[int, int, int] = gc.get_threshold()
        enabled: bool = gc.isenabled()
        freeze_count: int = gc.get_freeze_count() if hasattr(gc, "get_freeze_count") else 0
        stats: list[dict[str, int]] = gc.get_stats()
        total_collections: int = sum(int(s.get("collections", 0)) for s in stats)

        config_handle: ddup.SampleHandle = ddup.SampleHandle()
        # Count field carries this interval's explicit gc.collect() tally.
        # push_walltime(0, n) adds n to wall_count and 0 ns.
        config_handle.push_walltime(0, explicit)
        config_handle.push_frame(_GC_CONFIG_FRAME, _GC_FILE, 0, 0)
        config_handle.push_monotonic_ns(time.monotonic_ns())
        config_handle.flush_sample()

        LOG.debug(
            "GCCollector snapshot: enabled=%s thresholds=%s freeze=%d total_collections=%d explicit_collect=%d",
            enabled,
            thresholds,
            freeze_count,
            total_collections,
            explicit,
        )
