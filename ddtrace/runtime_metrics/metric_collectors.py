import os

from .collector import ValueCollector
from .constants import (
    GC_RUNTIME_METRICS,
    PSUTIL_RUNTIME_METRICS,
    GC_GEN1_COUNT,
    GC_GEN2_COUNT,
    GC_GEN3_COUNT,
    THREAD_COUNT,
    MEM_RSS,
    CTX_SWITCH_VOLUNTARY,
    CTX_SWITCH_INVOLUNTARY,
    CPU_TIME_SYS,
    CPU_TIME_USER,
    CPU_PERCENT,
)


class RuntimeMetricCollector(ValueCollector):
    value = {}


class GCRuntimeMetricCollector(RuntimeMetricCollector):
    """
    """
    required_modules = ['gc']
    periodic = True

    def collect_fn(self, keys):
        """Returns the gc count of the collections of the first 3 generations.
        More information:
            - https://docs.python.org/3/library/gc.html

        Metrics collected are:
        - gc.gen1_count
        - gc.gen2_count
        - gc.gen3_count
        """
        gc = self.modules.get('gc')

        # DEV: shortcut if none of the keys are required.
        if not set(keys) <= GC_RUNTIME_METRICS:
            return {}

        counts = gc.get_count()
        metrics = {
            GC_GEN1_COUNT: counts[0],
            GC_GEN2_COUNT: counts[1],
            GC_GEN3_COUNT: counts[2],
        }

        # filter metrics based on keys
        metrics = {k: metrics[k] for k in metrics if k in keys}

        return metrics


class PSUtilRuntimeMetricCollector(RuntimeMetricCollector):
    """Collector for psutil metrics.

    Performs batched operations via proc.oneshot() to optimize the calls.
    See https://psutil.readthedocs.io/en/latest/#psutil.Process.oneshot
    for more information.

    Metrics supported are:
    - thread_count
    - mem.rss
    """
    required_modules = ['psutil']
    periodic = True

    def _on_modules_load(self):
        self.proc = self.modules['psutil'].Process(os.getpid())

    def collect_fn(self, keys):
        with self.proc.oneshot():
            # gather all metrics
            metrics = {
                THREAD_COUNT: self.proc.num_threads(),
                MEM_RSS: self.proc.memory_info().rss,
                CTX_SWITCH_VOLUNTARY: self.proc.num_ctx_switches().voluntary,
                CTX_SWITCH_INVOLUNTARY: self.proc.num_ctx_switches().involuntary,
                CPU_TIME_SYS: self.proc.cpu_times().user,
                CPU_TIME_USER: self.proc.cpu_times().system,
                CPU_PERCENT: self.proc.cpu_percent(),
            }

            # filter metrics based on keys
            metrics = {k: metrics[k] for k in metrics if k in keys}

            return metrics
