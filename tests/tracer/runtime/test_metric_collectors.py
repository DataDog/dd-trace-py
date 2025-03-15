from ddtrace.internal.runtime.constants import CPU_PERCENT
from ddtrace.internal.runtime.constants import GC_COUNT_GEN0
from ddtrace.internal.runtime.constants import GC_RUNTIME_METRICS
from ddtrace.internal.runtime.constants import MEM_RSS
from ddtrace.internal.runtime.constants import PSUTIL_RUNTIME_METRICS
from ddtrace.internal.runtime.constants import THREAD_COUNT
from ddtrace.internal.runtime.metric_collectors import GCRuntimeMetricCollector
from ddtrace.internal.runtime.metric_collectors import PSUtilRuntimeMetricCollector
from ddtrace.internal.runtime.metric_collectors import RuntimeMetricCollector
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
        import os
        import threading
        import time

        from ddtrace.vendor import psutil

        # Something to bump CPU utilization
        def busy_wait(duration_ms):
            end_time = time.time() + (duration_ms / 1000.0)
            while time.time() < end_time:
                pass

        def get_metrics():
            # need to waste a reading of psutil because some of its reading have
            # memory and need a previous state
            collector = PSUtilRuntimeMetricCollector()
            collector.collect_fn(None)  # wasted
            proc = psutil.Process(os.getpid())
            proc.cpu_percent()  # wasted

            # Create some load.  If the duration is too low, then it can cause
            # wildly different values between readings.
            busy_wait(50)

            runtime_metrics = dict(collector.collect_fn(None))

            with proc.oneshot():
                psutil_metrics = {
                    CPU_PERCENT: proc.cpu_percent(),
                    MEM_RSS: proc.memory_info().rss,
                    THREAD_COUNT: proc.num_threads(),
                }
            return runtime_metrics, psutil_metrics

        def check_metrics(runtime_metrics, psutil_metrics):
            def within_threshold(a, b, epsilon):
                return abs(a - b) <= epsilon * max(abs(a), abs(b))

            # Number of threads should be precise
            if psutil_metrics[THREAD_COUNT] != runtime_metrics[THREAD_COUNT]:
                return False

            # CPU and RAM should be approximate.  These tests are checking that the category of
            # the value is correct, rather than the specific value itself.
            epsilon = 0.25
            if not within_threshold(psutil_metrics[CPU_PERCENT], runtime_metrics[CPU_PERCENT], epsilon):
                return False

            if not within_threshold(psutil_metrics[MEM_RSS], runtime_metrics[MEM_RSS], epsilon):
                return False

            return True

        # Sanity-check that the num_threads comparison works
        rt_metrics, pu_metrics = get_metrics()
        pu_metrics[THREAD_COUNT] += 1
        self.assertFalse(check_metrics(rt_metrics, pu_metrics))

        # Check that the CPU comparison works
        rt_metrics, pu_metrics = get_metrics()
        pu_metrics[CPU_PERCENT] *= 2
        self.assertFalse(check_metrics(rt_metrics, pu_metrics))

        # Check that the memory comparison works
        rt_metrics, pu_metrics = get_metrics()
        pu_metrics[MEM_RSS] *= 2
        self.assertFalse(check_metrics(rt_metrics, pu_metrics))

        # Baseline check
        self.assertTrue(check_metrics(*get_metrics()))

        # Check for threads.  Rather than using a sleep() which might be brittle in CI, use an explicit
        # semaphore as a stop condition per thread.
        def thread_stopper(stop_event):
            stop_event.wait()

        stop_event = threading.Event()
        threads = [threading.Thread(target=thread_stopper, args=(stop_event,)) for _ in range(10)]
        _ = [thread.start() for thread in threads]
        self.assertTrue(check_metrics(*get_metrics()))
        stop_event.set()
        _ = [thread.join() for thread in threads]

        # FIXME: this assertion is prone to failure
        """
        # Check for RSS
        wasted_memory = [" "] * 16 * 1024**2  # 16 megs
        self.assertTrue(check_metrics(*get_metrics()))
        del wasted_memory
        """


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
