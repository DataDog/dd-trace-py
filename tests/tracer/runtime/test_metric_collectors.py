import mock

from ddtrace.internal.runtime.constants import CPU_PERCENT
from ddtrace.internal.runtime.constants import GC_COUNT_GEN0
from ddtrace.internal.runtime.constants import GC_RUNTIME_METRICS
from ddtrace.internal.runtime.constants import MEM_RSS
from ddtrace.internal.runtime.constants import PSUTIL_RUNTIME_METRICS
from ddtrace.internal.runtime.constants import THREAD_COUNT
from ddtrace.internal.runtime.metric_collectors import GCRuntimeMetricCollector
from ddtrace.internal.runtime.metric_collectors import PSUtilRuntimeMetricCollector
from ddtrace.internal.runtime.metric_collectors import RuntimeMetricCollector
from ddtrace.vendor import psutil
from tests.utils import BaseTestCase


class TestRuntimeMetricCollector(BaseTestCase):
    def test_failed_module_load_collect(self):
        """Attempts to collect from a collector when it has failed to load its
        module should return no metrics gracefully.
        """

        class A(RuntimeMetricCollector):
            required_modules = ["moduleshouldnotexist"]

            def collect_fn(self, keys):
                return {"k": "v"}

        self.assertIsNotNone(A().collect(), "collect should return valid metrics")


class TestPSUtilRuntimeMetricCollector(BaseTestCase):
    def test_metrics(self):
        collector = PSUtilRuntimeMetricCollector()
        for metric_name, value in collector.collect(PSUTIL_RUNTIME_METRICS):
            self.assertIsNotNone(value)
            self.assertRegex(metric_name, r"^runtime.python\..*")

    def test_static_metrics(self):
        """Verify that PSUtilRuntimeMetricCollector captures mocked psutil values in runtime metrics."""
        # Mock values for psutil methods
        mock_cpu_percent = 50.0
        mock_memory_rss = 1024 * 1024 * 100  # 100 MB
        mock_thread_count = 5

        # Create a mock memory_info object that returns rss
        mock_memory_info = mock.Mock()
        mock_memory_info.rss = mock_memory_rss

        # Mock psutil Process methods globally
        with (
            mock.patch.object(psutil.Process, "cpu_percent", return_value=mock_cpu_percent),
            mock.patch.object(psutil.Process, "memory_info", return_value=mock_memory_info),
            mock.patch.object(psutil.Process, "num_threads", return_value=mock_thread_count),
        ):
            # Initialize collector - it will create a Process instance that uses our mocked methods
            collector = PSUtilRuntimeMetricCollector()
            # Get metrics from collector
            runtime_metrics = dict(collector.collect_fn(None))

        # Assert that ddtrace adds the mocked values to the runtime metrics dict
        self.assertIn(THREAD_COUNT, runtime_metrics)
        self.assertEqual(
            runtime_metrics[THREAD_COUNT],
            mock_thread_count,
            "Thread count should match mocked value",
        )

        self.assertIn(MEM_RSS, runtime_metrics)
        self.assertEqual(
            runtime_metrics[MEM_RSS],
            mock_memory_rss,
            "Memory RSS should match mocked value",
        )

        self.assertIn(CPU_PERCENT, runtime_metrics)
        self.assertEqual(
            runtime_metrics[CPU_PERCENT],
            mock_cpu_percent,
            "CPU percent should match mocked value",
        )


class TestGCRuntimeMetricCollector(BaseTestCase):
    def test_metrics(self):
        collector = GCRuntimeMetricCollector()
        for metric_name, value in collector.collect(GC_RUNTIME_METRICS):
            self.assertIsNotNone(value)
            self.assertRegex(metric_name, r"^runtime.python\..*")

    def test_gen1_changes(self):
        # disable gc
        import gc

        gc.disable()

        # start collector and get current gc counts
        collector = GCRuntimeMetricCollector()
        gc.collect()
        start = gc.get_count()

        # create reference
        a = []
        collected = collector.collect([GC_COUNT_GEN0])
        self.assertGreaterEqual(collected[0][1], start[0])

        # delete reference and collect
        del a
        gc.collect()
        collected_after = collector.collect([GC_COUNT_GEN0])
        assert len(collected_after) == 1
        assert collected_after[0][0] == "runtime.python.gc.count.gen0"
        assert isinstance(collected_after[0][1], int)
