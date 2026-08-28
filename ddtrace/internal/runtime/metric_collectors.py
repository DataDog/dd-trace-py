import time
from types import ModuleType
from typing import NamedTuple
from typing import Optional

from .. import forksafe
from .collector import ValueCollector
from .constants import CPU_PERCENT
from .constants import CPU_TIME_SYS
from .constants import CPU_TIME_USER
from .constants import CTX_SWITCH_INVOLUNTARY
from .constants import CTX_SWITCH_VOLUNTARY
from .constants import GC_COLLECTIONS_GEN0
from .constants import GC_COLLECTIONS_GEN1
from .constants import GC_COLLECTIONS_GEN2
from .constants import GC_COUNT_GEN0
from .constants import GC_COUNT_GEN1
from .constants import GC_COUNT_GEN2
from .constants import GC_PAUSE_MAX
from .constants import GC_PAUSE_TIME
from .constants import MEM_RSS
from .constants import THREAD_COUNT
from .gc_monitor import GEN_COUNT
from .gc_monitor import GCPauseMonitor
from .gc_monitor import GCPauseSnapshot
from .gc_monitor import gc_pause_monitor


class RuntimeMetricCollector(ValueCollector):
    value = []  # type: list[tuple[str, str]]
    periodic = True


def _read_gc_collections(gc_mod: ModuleType) -> list[int]:
    """Return per-generation ``collections`` counts from ``gc.get_stats()``."""
    stats: list[dict[str, int]] = gc_mod.get_stats()
    collections: list[int] = []
    for i in range(GEN_COUNT):
        row: dict[str, int] = stats[i] if i < len(stats) else {}
        collections.append(int(row.get("collections", 0)))
    return collections


def _delta(current: list[int], previous: list[int]) -> list[int]:
    return [max(0, c - p) for c, p in zip(current, previous)]


class GCRuntimeMetricCollector(RuntimeMetricCollector):
    """Collector for CPython GC counts, collection stats, and STW pause time.

    ``gc.count.genN`` remains the ``gc.get_count()`` allocation counters.
    Collection/pause metrics are interval deltas, matching CPU time.
    """

    required_modules = ["gc"]
    _monitor: Optional[GCPauseMonitor] = None
    _prev_collections: list[int]

    def _on_modules_load(self) -> None:
        self._monitor: GCPauseMonitor = gc_pause_monitor()
        self._monitor.acquire()
        gc_mod: ModuleType = self.modules["gc"]
        self._prev_collections: list[int] = _read_gc_collections(gc_mod)
        forksafe.register(self._reset_state)

    def _reset_state(self) -> None:
        gc_mod: Optional[ModuleType] = self.modules.get("gc")
        if gc_mod is None:
            return
        self._prev_collections: list[int] = _read_gc_collections(gc_mod)

    def stop(self) -> None:
        monitor: Optional[GCPauseMonitor] = self._monitor
        if monitor is not None:
            self._monitor = None
            monitor.release()

    def collect_fn(self, keys: Optional[set[str]]) -> list[tuple[str, int]]:
        gc_mod: ModuleType = self.modules["gc"]

        counts: tuple[int, int, int] = gc_mod.get_count()
        collections: list[int] = _read_gc_collections(gc_mod)
        d_collections: list[int] = _delta(collections, self._prev_collections)
        self._prev_collections: list[int] = collections

        pause: Optional[GCPauseSnapshot]
        if self._monitor is not None:
            pause = self._monitor.snapshot_and_reset()
        else:
            pause = None

        metrics: list[tuple[str, int]] = [
            (GC_COUNT_GEN0, counts[0]),
            (GC_COUNT_GEN1, counts[1]),
            (GC_COUNT_GEN2, counts[2]),
            (GC_COLLECTIONS_GEN0, d_collections[0]),
            (GC_COLLECTIONS_GEN1, d_collections[1]),
            (GC_COLLECTIONS_GEN2, d_collections[2]),
            (GC_PAUSE_TIME, 0 if pause is None else pause.total_ns),
            (GC_PAUSE_MAX, 0 if pause is None else pause.max_ns),
        ]

        return metrics


class _ProcessMetrics(NamedTuple):
    """Named view over the plain tuple returned by native.process_metrics(), for
    readability at the one call site that reads these fields.
    """

    cpu_time_sys_ns: int
    cpu_time_user_ns: int
    ctx_switches_voluntary: int
    ctx_switches_involuntary: int
    num_threads: int
    rss_bytes: int


class NativeProcessMetricCollector(RuntimeMetricCollector):
    """
    Collector for process-level metrics (cpu time, memory, threads, context
    switches).
    """

    required_modules = ["ddtrace.internal.native"]

    _NS_TO_SEC = 1e-9

    def _on_modules_load(self):
        # `_reset_state` doubles as the smoke test: if it raises, `_load_modules`'s caller
        # never sees it since it's not an ImportError, so surface it the same way a failed
        # import would.
        try:
            self._reset_state()
        except Exception:
            self.enabled = False
            return

        forksafe.register(self._reset_state)

    def _reset_state(self):
        # Seed the baselines from a fresh reading instead of zero, both here and on fork:
        # a forked child inherits these as the parent's last-observed values, while its own
        # counters (e.g. Linux's /proc/self/stat) restart near zero, so an unseeded baseline
        # would make the very next collect_fn call report a huge one-off delta -- the
        # process's entire lifetime CPU time/ctx switches so far, rather than the delta since
        # enablement/fork.
        native = self.modules["ddtrace.internal.native"]
        process_metrics = _ProcessMetrics(*native.process_metrics())
        self.stored_cpu_times = {
            CPU_TIME_SYS: process_metrics.cpu_time_sys_ns * self._NS_TO_SEC,
            CPU_TIME_USER: process_metrics.cpu_time_user_ns * self._NS_TO_SEC,
        }
        self.stored_ctx_switches = {
            CTX_SWITCH_VOLUNTARY: process_metrics.ctx_switches_voluntary,
            CTX_SWITCH_INVOLUNTARY: process_metrics.ctx_switches_involuntary,
        }
        self._last_wall_time = time.monotonic()

    def collect_fn(self, keys):
        native = self.modules["ddtrace.internal.native"]

        process_metrics = _ProcessMetrics(*native.process_metrics())

        now = time.monotonic()
        elapsed = now - self._last_wall_time
        self._last_wall_time = now

        cpu_time_sys = process_metrics.cpu_time_sys_ns * self._NS_TO_SEC
        cpu_time_user = process_metrics.cpu_time_user_ns * self._NS_TO_SEC
        delta_cpu_time_sys = cpu_time_sys - self.stored_cpu_times[CPU_TIME_SYS]
        delta_cpu_time_user = cpu_time_user - self.stored_cpu_times[CPU_TIME_USER]
        self.stored_cpu_times[CPU_TIME_SYS] = cpu_time_sys
        self.stored_cpu_times[CPU_TIME_USER] = cpu_time_user

        metrics = {
            CPU_TIME_SYS: delta_cpu_time_sys,
            CPU_TIME_USER: delta_cpu_time_user,
            THREAD_COUNT: process_metrics.num_threads,
            MEM_RSS: process_metrics.rss_bytes,
            CPU_PERCENT: (delta_cpu_time_sys + delta_cpu_time_user) / elapsed * 100 if elapsed > 0 else 0.0,
        }

        # A negative value means the platform can't report ctx switches (see
        # src/native/process_metrics/mod.rs) -- omit those two metrics rather
        # than fabricate a delta.
        if process_metrics.ctx_switches_voluntary >= 0:
            metrics[CTX_SWITCH_VOLUNTARY] = (
                process_metrics.ctx_switches_voluntary - self.stored_ctx_switches[CTX_SWITCH_VOLUNTARY]
            )
            self.stored_ctx_switches[CTX_SWITCH_VOLUNTARY] = process_metrics.ctx_switches_voluntary
        if process_metrics.ctx_switches_involuntary >= 0:
            metrics[CTX_SWITCH_INVOLUNTARY] = (
                process_metrics.ctx_switches_involuntary - self.stored_ctx_switches[CTX_SWITCH_INVOLUNTARY]
            )
            self.stored_ctx_switches[CTX_SWITCH_INVOLUNTARY] = process_metrics.ctx_switches_involuntary

        return list(metrics.items())
