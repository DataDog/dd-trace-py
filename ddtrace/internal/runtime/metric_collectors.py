import os
from typing import Callable
from typing import Optional

from .collector import ValueCollector
from .constants import CPU_PERCENT
from .constants import CPU_TIME_SYS
from .constants import CPU_TIME_USER
from .constants import CTX_SWITCH_INVOLUNTARY
from .constants import CTX_SWITCH_VOLUNTARY
from .constants import GC_COUNT_GEN0
from .constants import GC_COUNT_GEN1
from .constants import GC_COUNT_GEN2
from .constants import MEM_RSS
from .constants import PROFILER_ASYNCIO_TASK_COUNT
from .constants import PROFILER_COPY_MEMORY_ERROR_COUNT
from .constants import PROFILER_FAST_COPY_MEMORY_ENABLED
from .constants import PROFILER_GREENLET_COUNT
from .constants import PROFILER_HEAP_TRACKER_COUNT
from .constants import PROFILER_SAMPLE_CAPTURE_CPU_TIME_US
from .constants import PROFILER_SAMPLE_COUNT
from .constants import PROFILER_SAMPLING_EVENT_COUNT
from .constants import PROFILER_SAMPLING_INTERVAL_US
from .constants import PROFILER_STRING_TABLE_COUNT
from .constants import PROFILER_STRING_TABLE_EPHEMERAL_COUNT
from .constants import THREAD_COUNT


class RuntimeMetricCollector(ValueCollector):
    value = []  # type: list[tuple[str, str]]
    periodic = True


class GCRuntimeMetricCollector(RuntimeMetricCollector):
    """Collector for garbage collection generational counts

    More information at https://docs.python.org/3/library/gc.html
    """

    required_modules = ["gc"]

    def collect_fn(self, keys):
        gc = self.modules.get("gc")

        counts = gc.get_count()
        metrics = [
            (GC_COUNT_GEN0, counts[0]),
            (GC_COUNT_GEN1, counts[1]),
            (GC_COUNT_GEN2, counts[2]),
        ]

        return metrics


class PSUtilRuntimeMetricCollector(RuntimeMetricCollector):
    """Collector for psutil metrics.

    Performs batched operations via proc.oneshot() to optimize the calls.
    See https://psutil.readthedocs.io/en/latest/#psutil.Process.oneshot
    for more information.
    """

    required_modules = ["ddtrace.vendor.psutil"]
    delta_funs = {
        CPU_TIME_SYS: lambda p: p.cpu_times().system,
        CPU_TIME_USER: lambda p: p.cpu_times().user,
        CTX_SWITCH_VOLUNTARY: lambda p: p.num_ctx_switches().voluntary,
        CTX_SWITCH_INVOLUNTARY: lambda p: p.num_ctx_switches().involuntary,
    }
    abs_funs = {
        THREAD_COUNT: lambda p: p.num_threads(),
        MEM_RSS: lambda p: p.memory_info().rss,
        CPU_PERCENT: lambda p: p.cpu_percent(),
    }

    def _on_modules_load(self):
        self.proc = self.modules["ddtrace.vendor.psutil"].Process(os.getpid())
        self.stored_values = {key: 0 for key in self.delta_funs.keys()}

    def collect_fn(self, keys):
        with self.proc.oneshot():
            metrics = {}

            # Populate metrics for which we compute delta values
            for metric, delta_fun in self.delta_funs.items():
                try:
                    value = delta_fun(self.proc)
                except Exception:
                    value = 0

                delta = value - self.stored_values.get(metric, 0)
                self.stored_values[metric] = value
                metrics[metric] = delta

            # Populate metrics that just take instantaneous reading
            for metric, abs_fun in self.abs_funs.items():
                try:
                    value = abs_fun(self.proc)
                except Exception:
                    value = 0

                metrics[metric] = value

            return list(metrics.items())


class ProfilerRuntimeMetricCollector(RuntimeMetricCollector):
    """Collector for profiler operational metrics.

    Reads cumulative counters and gauge snapshots from the native profiler
    via ddup.get_profiler_runtime_stats(). Counter metrics are emitted as
    per-interval deltas; gauge metrics are emitted as absolute values.
    """

    _COUNTER_KEYS = {
        "sample_count": PROFILER_SAMPLE_COUNT,
        "sampling_event_count": PROFILER_SAMPLING_EVENT_COUNT,
        "copy_memory_error_count": PROFILER_COPY_MEMORY_ERROR_COUNT,
        "sample_capture_cpu_time_us": PROFILER_SAMPLE_CAPTURE_CPU_TIME_US,
    }
    _GAUGE_KEYS = {
        "sampling_interval_us": PROFILER_SAMPLING_INTERVAL_US,
        "asyncio_task_count": PROFILER_ASYNCIO_TASK_COUNT,
        "greenlet_count": PROFILER_GREENLET_COUNT,
        "heap_tracker_count": PROFILER_HEAP_TRACKER_COUNT,
        "string_table_count": PROFILER_STRING_TABLE_COUNT,
        "string_table_ephemeral_count": PROFILER_STRING_TABLE_EPHEMERAL_COUNT,
        "fast_copy_memory_enabled": PROFILER_FAST_COPY_MEMORY_ENABLED,
    }

    def __init__(self):
        super().__init__()
        self._get_stats: Optional[Callable[[], Optional[dict[str, int]]]] = None
        self._stored_values: dict[str, int] = {key: 0 for key in self._COUNTER_KEYS}

    def _ensure_stats_fn(self) -> bool:
        if self._get_stats is not None:
            return True
        try:
            from ddtrace.internal.datadog.profiling.ddup._ddup import get_profiler_runtime_stats

            self._get_stats = get_profiler_runtime_stats
            return True
        except ImportError:
            return False

    def collect_fn(self, keys):
        if not self._ensure_stats_fn():
            return []

        get_stats = self._get_stats
        if get_stats is None:
            return []
        stats = get_stats()
        if stats is None:
            return []

        metrics = []

        for raw_key, metric_name in self._COUNTER_KEYS.items():
            cumulative = stats.get(raw_key, 0)
            prev = self._stored_values.get(raw_key, 0)
            delta = cumulative - prev
            if delta < 0:
                delta = cumulative
            self._stored_values[raw_key] = cumulative
            metrics.append((metric_name, delta))

        for raw_key, metric_name in self._GAUGE_KEYS.items():
            if raw_key in stats:
                metrics.append((metric_name, stats[raw_key]))

        return metrics
