import time
from typing import NamedTuple

from .. import forksafe
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
        # Smoke test: if this raises, `_load_modules`'s caller never sees it since it's
        # not an ImportError, so surface it the same way a failed import would.
        try:
            self.modules["ddtrace.internal.native"].process_metrics()
        except Exception:
            self.enabled = False
            return

        self._reset_state()
        forksafe.register(self._reset_state)

    def _reset_state(self):
        # A forked child inherits these as the parent's last-observed values, while its own
        # /proc/self/stat counters restart near zero -- without resetting here, the child's
        # first post-fork delta would be negative.
        self.stored_cpu_times = {CPU_TIME_SYS: 0.0, CPU_TIME_USER: 0.0}
        self.stored_ctx_switches = {CTX_SWITCH_VOLUNTARY: 0, CTX_SWITCH_INVOLUNTARY: 0}
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
