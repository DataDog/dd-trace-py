import time

from ddtrace.runtime_metrics.metric_collectors import (
    RuntimeMetricCollector,
    GCRuntimeMetricCollector,
    PSUtilRuntimeMetricCollector,
)

from ddtrace.runtime_metrics.constants import (
    GC_GEN1_COUNT,
    GC_RUNTIME_METRICS,
    PSUTIL_RUNTIME_METRICS,
    DEFAULT_RUNTIME_METRICS,
)
from ..base import BaseTestCase


class TestRuntimeMetricCollector(BaseTestCase):
    def test_failed_module_load_collect(self):
        """Attempts to collect from a collector when it has failed to load its
        module should return no metrics gracefully.
        """
        def collect(modules, keys):
            return {'k': 'v'}

        vc = RuntimeMetricCollector(collect_fn=collect, required_modules=['moduleshouldnotexist'])
        self.assertIsNotNone(vc.collect(), 'collect should return valid metrics')

class TestPSUtilRuntimeMetricCollector(BaseTestCase):
    def test_metrics(self):
        collector = PSUtilRuntimeMetricCollector()
        collected = collector.collect(PSUTIL_RUNTIME_METRICS)
        for metric in PSUTIL_RUNTIME_METRICS:
            self.assertIsNotNone(collected[metric])

class TestGCRuntimeMetricCollector(BaseTestCase):
    def test_metrics(self):
        collector = GCRuntimeMetricCollector()
        collected = collector.collect(GC_RUNTIME_METRICS)
        for metric in GC_RUNTIME_METRICS:
            self.assertIsNotNone(collected[metric])

    def test_gen1_changes(self):
        # disable gc
        import gc; gc.disable()

        # start collector and get current gc counts
        collector = GCRuntimeMetricCollector()
        gc.collect()
        start = gc.get_count()

        # create reference
        a = []
        collected = collector.collect([GC_GEN1_COUNT])
        self.assertGreater(collected[GC_GEN1_COUNT], start[0])

        # delete reference and collect
        del a
        gc.collect()
        collected_after = collector.collect([GC_GEN1_COUNT])
        self.assertLess(collected_after[GC_GEN1_COUNT], collected[GC_GEN1_COUNT])
