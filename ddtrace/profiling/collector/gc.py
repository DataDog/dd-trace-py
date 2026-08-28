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


class GCCollector(collector.Collector):
    """Collect CPython GC pause durations and explicit gc.collect() call counts.

    Hooks gc.callbacks for per-collection events and emits a snapshot sample
    once per profile flush interval.

    Data emitted:
    - Wall time samples (push_walltime) attributed to synthetic gc.collect[gen=N]
      frames. These appear in the Wall Time profile view.
    - Alloc samples (push_alloc) that put collected-object count in the
      alloc-space field so they appear in the Alloc profile view under the
      same frames.
    - A gc.config snapshot sample per flush carrying the interval's explicit
      gc.collect() tally in the sample count field.

    Thresholds, freeze count, and cumulative collection totals are DEBUG-only.
    """

    _lock: threading.Lock
    _start_ns: dict[int, int]
    _explicit_count: int
    _orig_collect: Callable[..., int]
    _installed_collect: Callable[..., int]

    def _start_service(self) -> None:
        self._lock = threading.Lock()
        self._start_ns = {}
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
            self._start_ns.clear()
            self._explicit_count = 0

    def _patched_collect(self, generation: int = 2) -> int:
        with self._lock:
            self._explicit_count += 1
        return self._orig_collect(generation)

    def _on_gc(self, phase: str, info: dict[str, int]) -> None:
        gen: int = info.get("generation", 0)
        if phase == "start":
            self._start_ns[gen] = time.monotonic_ns()
        elif phase == "stop":
            start: int | None = self._start_ns.pop(gen, None)
            if start is None:
                return
            pause_ns: int = time.monotonic_ns() - start
            frame_name: str = _GEN_NAMES[gen] if gen < len(_GEN_NAMES) else "gc.collect"

            handle: ddup.SampleHandle = ddup.SampleHandle()
            handle.push_walltime(pause_ns, 1)
            handle.push_frame(frame_name, "gc", 0, gen)
            handle.push_monotonic_ns(time.monotonic_ns())
            handle.flush_sample()

            collected: int = info.get("collected", 0)
            if collected > 0:
                handle2: ddup.SampleHandle = ddup.SampleHandle()
                handle2.push_alloc(collected, 1)
                handle2.push_frame(frame_name, "gc", 0, gen)
                handle2.push_monotonic_ns(time.monotonic_ns())
                handle2.flush_sample()

    def snapshot(self) -> None:  # type: ignore[override]
        with self._lock:
            explicit: int = self._explicit_count
            self._explicit_count = 0

        thresholds: tuple[int, int, int] = gc.get_threshold()
        enabled: bool = gc.isenabled()
        freeze_count: int = gc.get_freeze_count() if hasattr(gc, "get_freeze_count") else 0
        stats: list[dict[str, int]] = gc.get_stats()
        total_collections: int = sum(int(s.get("collections", 0)) for s in stats)

        handle: ddup.SampleHandle = ddup.SampleHandle()
        # Use count field to carry explicit gc.collect() tally for this interval.
        # A zero walltime with count > 0 is the established pattern for pure-count
        # samples (same as lock release-time samples with zero duration).
        handle.push_walltime(0, explicit)
        handle.push_frame("gc.config", "gc", 0, 0)
        handle.push_monotonic_ns(time.monotonic_ns())
        handle.flush_sample()

        LOG.debug(
            "GCCollector snapshot: enabled=%s thresholds=%s freeze=%d total_collections=%d explicit_collect=%d",
            enabled,
            thresholds,
            freeze_count,
            total_collections,
            explicit,
        )
