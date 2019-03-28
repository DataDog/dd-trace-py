from ddtrace.runtime_metrics.runtime_metrics import (
    RuntimeTags,
    RuntimeMetrics,
    RuntimeMetricsWorker,
)
from ddtrace.runtime_metrics.constants import (
    DEFAULT_RUNTIME_METRICS,
    DEFAULT_RUNTIME_TAGS,
    GC_GEN1_COUNT,
    RUNTIME_ID,
)
from ..base import (
    BaseTestCase,
    BaseTracerTestCase,
)

class TestRuntimeTags(BaseTracerTestCase):
    def test_all_tags(self):
        with self.override_global_tracer():
            with self.trace('test', service='test'):
                tags = set([k for (k,v) in RuntimeTags()])
                self.assertSetEqual(tags, DEFAULT_RUNTIME_TAGS)

    def test_one_tag(self):
        with self.override_global_tracer():
            with self.trace('test', service='test'):
                tags = [k for (k,v) in RuntimeTags(enabled=[RUNTIME_ID])]
                self.assertEqual(tags, [RUNTIME_ID])

class TestRuntimeMetrics(BaseTestCase):
    def test_all_metrics(self):
        metrics = set([k for (k,v) in RuntimeMetrics()])
        self.assertSetEqual(metrics, DEFAULT_RUNTIME_METRICS)

    def test_one_metric(self):
        metrics = [k for (k,v) in RuntimeMetrics(enabled=[GC_GEN1_COUNT])]
        self.assertEqual(metrics, [GC_GEN1_COUNT])
