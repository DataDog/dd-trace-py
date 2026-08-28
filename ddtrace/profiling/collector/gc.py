from __future__ import annotations

import logging
import threading
import time
from typing import Any
from typing import Optional

from ddtrace.internal.datadog.profiling import ddup
from ddtrace.internal.runtime.gc_monitor import GEN_COUNT
from ddtrace.internal.runtime.gc_monitor import GCPauseMonitor
from ddtrace.internal.runtime.gc_monitor import gc_pause_monitor
from ddtrace.internal.settings.profiling import config
from ddtrace.profiling import collector


LOG = logging.getLogger(__name__)

# Shared with the stack sampler's synthetic GC frame (PR 19190) so both
# features aggregate in the same flame-graph bucket.
_GC_FRAME_NAME = "Garbage collection"
_GC_FRAME_FILE = "<runtime>"


class _GenStats:
    __slots__ = ("count", "total_ns", "max_ns")

    def __init__(self) -> None:
        self.count = 0
        self.total_ns = 0
        self.max_ns = 0


class GCCollector(collector.Collector):
    """Collect exact GC pause durations via the process-wide GCPauseMonitor.

    Subscribes as a listener so runtime metrics and profiling share one
    ``gc.callbacks`` entry. Hot path: accumulate per generation, or emit a
    timestamped sample if the pause meets ``pause_threshold_ns``. Snapshot
    drains aggregates once per upload.

    Individual long-pause samples are excluded from the aggregate so pprof
    stacks that share the synthetic GC frames do not double-count.
    """

    def __init__(
        self,
        tracer: Any = None,
        pause_threshold_ns: Optional[int] = None,
    ) -> None:
        super().__init__()
        self.tracer = tracer
        raw = pause_threshold_ns if pause_threshold_ns is not None else config.gc.pause_threshold_ns
        if raw < 0:
            raise ValueError("pause_threshold_ns must be >= 0")
        self.pause_threshold_ns = raw
        self._lock = threading.Lock()
        self._stats: list[_GenStats] = [_GenStats() for _ in range(GEN_COUNT)]
        self._monitor: Optional[GCPauseMonitor] = None

    def _start_service(self) -> None:
        self._monitor = gc_pause_monitor()
        self._monitor.add_listener(self._on_pause)
        self._monitor.acquire()
        LOG.debug("GCCollector started: pause_threshold_ns=%d", self.pause_threshold_ns)

    def _stop_service(self) -> None:
        monitor = self._monitor
        if monitor is not None:
            monitor.remove_listener(self._on_pause)
            monitor.release()
            self._monitor = None
        LOG.debug("GCCollector stopped")

    def _on_pause(self, gen: int, pause_ns: int, start_ns: int, frame: Any) -> None:
        if pause_ns >= self.pause_threshold_ns:
            self._emit(
                pause_ns,
                1,
                gen,
                timestamp_ns=time.monotonic_ns(),
                trigger_frame=frame,
            )
            return
        bucket = gen if 0 <= gen < GEN_COUNT else GEN_COUNT - 1
        with self._lock:
            stats = self._stats[bucket]
            stats.count += 1
            stats.total_ns += pause_ns
            if pause_ns > stats.max_ns:
                stats.max_ns = pause_ns

    def snapshot(self) -> None:  # type: ignore[override]
        with self._lock:
            stats = self._stats
            self._stats = [_GenStats() for _ in range(GEN_COUNT)]
        for gen, s in enumerate(stats):
            if s.count > 0:
                self._emit(s.total_ns, s.count, gen)
            if s.max_ns > 0:
                self._emit(s.max_ns, 1, gen, is_max=True)

    def _emit(
        self,
        duration_ns: int,
        count: int,
        gen: int,
        *,
        is_max: bool = False,
        timestamp_ns: Optional[int] = None,
        trigger_frame: Any = None,
    ) -> None:
        if not ddup.is_available:
            return
        try:
            handle = ddup.SampleHandle()
            handle.push_gc(duration_ns, count)

            thread = threading.current_thread()
            thread_id = thread.ident or 0
            native_id = getattr(thread, "native_id", 0) or 0
            handle.push_threadinfo(thread_id, native_id, thread.name or "")

            if self.tracer is not None:
                handle.push_span(self.tracer.current_span())

            handle.push_frame(_GC_FRAME_NAME, _GC_FRAME_FILE, 0, 0)
            if is_max:
                handle.push_frame("max", "gc", 0, 0)
            handle.push_frame(f"gen={gen}", "gc", 0, gen)
            if trigger_frame is not None:
                handle.push_pyframes(trigger_frame)

            if timestamp_ns is not None:
                handle.push_monotonic_ns(timestamp_ns)
            handle.flush_sample()
        except Exception:
            LOG.debug("GCCollector failed to emit sample", exc_info=True)
