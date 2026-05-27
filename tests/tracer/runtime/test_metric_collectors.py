from unittest import mock

from ddtrace.internal.runtime.constants import CPU_PERCENT
from ddtrace.internal.runtime.constants import GC_COUNT_GEN0
from ddtrace.internal.runtime.constants import GC_RUNTIME_METRICS
from ddtrace.internal.runtime.constants import MEM_RSS
from ddtrace.internal.runtime.constants import PROFILER_COPY_MEMORY_ERROR_COUNT
from ddtrace.internal.runtime.constants import PROFILER_GREENLET_COUNT
from ddtrace.internal.runtime.constants import PROFILER_RUNTIME_METRICS
from ddtrace.internal.runtime.constants import PROFILER_SAMPLE_CAPTURE_CPU_TIME_US
from ddtrace.internal.runtime.constants import PROFILER_SAMPLE_COUNT
from ddtrace.internal.runtime.constants import PROFILER_SAMPLING_EVENT_COUNT
from ddtrace.internal.runtime.constants import PROFILER_SAMPLING_INTERVAL_US
from ddtrace.internal.runtime.constants import PSUTIL_RUNTIME_METRICS
from ddtrace.internal.runtime.constants import THREAD_COUNT
from ddtrace.internal.runtime.metric_collectors import GCRuntimeMetricCollector
from ddtrace.internal.runtime.metric_collectors import ProfilerRuntimeMetricCollector
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


class TestProfilerRuntimeMetricCollector(BaseTestCase):
    def _make_collector(self, stats_fn):
        """Create a collector with an injected stats function."""
        collector = ProfilerRuntimeMetricCollector()
        collector._get_stats = stats_fn
        return collector

    def test_profiler_not_running(self):
        """When the profiler is not running, the collector returns no metrics."""
        collector = self._make_collector(lambda: None)
        metrics = collector.collect(PROFILER_RUNTIME_METRICS)
        self.assertEqual(metrics, [])

    def test_import_failure(self):
        """When ddup cannot be imported, the collector returns no metrics."""
        collector = ProfilerRuntimeMetricCollector()
        with mock.patch.dict("sys.modules", {"ddtrace.internal.datadog.profiling.ddup._ddup": None}):
            collector._get_stats = None
            metrics = collector.collect(PROFILER_RUNTIME_METRICS)
        self.assertEqual(metrics, [])

    def test_counter_delta_computation(self):
        """Counter metrics are emitted as per-interval deltas from cumulative values."""
        call_count = [0]
        cumulative_values = [
            {
                "sample_count": 10,
                "sampling_event_count": 3,
                "copy_memory_error_count": 0,
                "sample_capture_cpu_time_us": 500,
                "sampling_interval_us": 10000,
            },
            {
                "sample_count": 25,
                "sampling_event_count": 6,
                "copy_memory_error_count": 2,
                "sample_capture_cpu_time_us": 1200,
                "sampling_interval_us": 10000,
            },
        ]

        def mock_stats():
            idx = call_count[0]
            call_count[0] += 1
            return cumulative_values[idx]

        collector = self._make_collector(mock_stats)

        # First collection: deltas from zero
        metrics1 = dict(collector.collect_fn(None))
        self.assertEqual(metrics1[PROFILER_SAMPLE_COUNT], 10)
        self.assertEqual(metrics1[PROFILER_SAMPLING_EVENT_COUNT], 3)
        self.assertEqual(metrics1[PROFILER_COPY_MEMORY_ERROR_COUNT], 0)
        self.assertEqual(metrics1[PROFILER_SAMPLE_CAPTURE_CPU_TIME_US], 500)
        self.assertEqual(metrics1[PROFILER_SAMPLING_INTERVAL_US], 10000)

        # Second collection: deltas from previous cumulative values
        metrics2 = dict(collector.collect_fn(None))
        self.assertEqual(metrics2[PROFILER_SAMPLE_COUNT], 15)
        self.assertEqual(metrics2[PROFILER_SAMPLING_EVENT_COUNT], 3)
        self.assertEqual(metrics2[PROFILER_COPY_MEMORY_ERROR_COUNT], 2)
        self.assertEqual(metrics2[PROFILER_SAMPLE_CAPTURE_CPU_TIME_US], 700)

    def test_gauge_metrics_emitted(self):
        """Gauge metrics present in stats are emitted as absolute values."""
        collector = self._make_collector(
            lambda: {
                "sample_count": 0,
                "sampling_event_count": 0,
                "copy_memory_error_count": 0,
                "sample_capture_cpu_time_us": 0,
                "sampling_interval_us": 10000,
                "greenlet_count": 5,
            }
        )
        metrics = dict(collector.collect_fn(None))
        self.assertEqual(metrics[PROFILER_SAMPLING_INTERVAL_US], 10000)
        self.assertEqual(metrics[PROFILER_GREENLET_COUNT], 5)

    def test_optional_gauges_omitted(self):
        """Gauge keys absent from the stats dict (not yet set) are not emitted."""
        collector = self._make_collector(
            lambda: {
                "sample_count": 0,
                "sampling_event_count": 0,
                "copy_memory_error_count": 0,
                "sample_capture_cpu_time_us": 0,
            }
        )
        metrics = dict(collector.collect_fn(None))
        self.assertNotIn(PROFILER_SAMPLING_INTERVAL_US, metrics)
        self.assertNotIn(PROFILER_GREENLET_COUNT, metrics)

    def test_metric_names(self):
        """All emitted metrics follow the runtime.python.profiler.* naming convention."""
        collector = self._make_collector(
            lambda: {
                "sample_count": 5,
                "sampling_event_count": 1,
                "copy_memory_error_count": 0,
                "sample_capture_cpu_time_us": 100,
                "sampling_interval_us": 10000,
                "asyncio_task_count": 2,
                "greenlet_count": 3,
                "heap_tracker_count": 100,
                "string_table_count": 50,
                "string_table_ephemeral_count": 10,
                "fast_copy_memory_enabled": 1,
            }
        )
        for metric_name, value in collector.collect(PROFILER_RUNTIME_METRICS):
            self.assertRegex(metric_name, r"^runtime\.python\.profiler\..+")
