from __future__ import annotations

import gc
import logging
import threading
import time
from typing import Callable

from ddtrace.internal.datadog.profiling import ddup
from ddtrace.profiling import collector


LOG = logging.getLogger(__name__)

_GEN_NAMES: tuple[str, ...] = (
    "gc.collect[gen=0]",
    "gc.collect[gen=1]",
    "gc.collect[gen=2]",
)


class GCCollector(collector.Collector):
    """Collect GC pause durations, collection counts, and configuration state.

    Hooks gc.callbacks for per-collection events and emits a snapshot sample
    once per profile flush interval with cumulative counts and config state.

    Data emitted:
    - Wall time samples (push_walltime) attributed to synthetic gc.collect[gen=N]
      frames — appear in the Wall Time profile view.
    - Alloc samples (push_alloc) with collected-object count — appear in the
      Alloc profile view under the same frames.
    - A gc.config snapshot sample per flush carrying explicit gc.collect() call
      count in the sample count field.
    """

    def _start_service(self) -> None:
        self._start_ns: dict[int, int] = {}
        self._explicit_count: int = 0
        self._count_lock = threading.Lock()
        self._orig_collect: Callable[..., int] = gc.collect
        gc.collect = self._patched_collect
        gc.callbacks.append(self._on_gc)
        LOG.debug("GCCollector started")

    def _stop_service(self) -> None:
        try:
            gc.callbacks.remove(self._on_gc)
        except ValueError:
            pass
        gc.collect = self._orig_collect
        LOG.debug("GCCollector stopped")

    def _patched_collect(self, generation: int = 2) -> int:
        with self._count_lock:
            self._explicit_count += 1
        return self._orig_collect(generation)

    def _on_gc(self, phase: str, info: dict[str, int]) -> None:
        gen = info.get("generation", 0)
        if phase == "start":
            self._start_ns[gen] = time.monotonic_ns()
        elif phase == "stop":
            start = self._start_ns.pop(gen, None)
            if start is None:
                return
            pause_ns = time.monotonic_ns() - start
            frame_name = _GEN_NAMES[gen] if gen < len(_GEN_NAMES) else "gc.collect"

            handle = ddup.SampleHandle()
            handle.push_walltime(pause_ns, 1)
            handle.push_frame(frame_name, "gc", 0, 0)
            handle.push_monotonic_ns(time.monotonic_ns())
            handle.flush_sample()

            collected = info.get("collected", 0)
            if collected > 0:
                handle2 = ddup.SampleHandle()
                handle2.push_alloc(collected, 1)
                handle2.push_frame(frame_name, "gc", 0, 0)
                handle2.push_monotonic_ns(time.monotonic_ns())
                handle2.flush_sample()

    def snapshot(self) -> None:  # type: ignore[override]
        with self._count_lock:
            explicit = self._explicit_count
            self._explicit_count = 0

        handle = ddup.SampleHandle()
        # Use count field to carry explicit gc.collect() tally for this interval.
        # A zero walltime with count > 0 is the established pattern for pure-count
        # samples (same as lock release-time samples with zero duration).
        handle.push_walltime(0, explicit)
        handle.push_frame("gc.config", "gc", 0, 0)
        handle.push_monotonic_ns(time.monotonic_ns())
        handle.flush_sample()

        if LOG.isEnabledFor(logging.DEBUG):
            thresholds = gc.get_threshold()
            enabled = gc.isenabled()
            freeze_count = gc.get_freeze_count() if hasattr(gc, "get_freeze_count") else 0
            stats = gc.get_stats()
            total_collections = sum(s.get("collections", 0) for s in stats)
            LOG.debug(
                "GCCollector snapshot: enabled=%s thresholds=%s freeze=%d total_collections=%d explicit_collect=%d",
                enabled,
                thresholds,
                freeze_count,
                total_collections,
                explicit,
            )
