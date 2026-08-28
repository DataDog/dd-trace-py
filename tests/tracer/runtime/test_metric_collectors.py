import os
from unittest import mock

import pytest

from ddtrace.internal.runtime.constants import CPU_PERCENT
from ddtrace.internal.runtime.constants import GC_COLLECTIONS_GEN0
from ddtrace.internal.runtime.constants import GC_COLLECTIONS_GEN1
from ddtrace.internal.runtime.constants import GC_COLLECTIONS_GEN2
from ddtrace.internal.runtime.constants import GC_COUNT_GEN0
from ddtrace.internal.runtime.constants import GC_PAUSE_MAX
from ddtrace.internal.runtime.constants import GC_PAUSE_TIME
from ddtrace.internal.runtime.constants import GC_RUNTIME_METRICS
from ddtrace.internal.runtime.constants import MEM_RSS
from ddtrace.internal.runtime.constants import NATIVE_PROCESS_RUNTIME_METRICS
from ddtrace.internal.runtime.constants import THREAD_COUNT
from ddtrace.internal.runtime.metric_collectors import GCRuntimeMetricCollector
from ddtrace.internal.runtime.metric_collectors import NativeProcessMetricCollector
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


class TestNativeProcessMetricCollector(BaseTestCase):
    def test_metrics(self):
        collector = NativeProcessMetricCollector()
        for metric_name, value in collector.collect(NATIVE_PROCESS_RUNTIME_METRICS):
            self.assertIsNotNone(value)
            self.assertRegex(metric_name, r"^runtime.python\..*")

    def test_static_metrics(self):
        """Verify that NativeProcessMetricCollector reports mocked native.process_metrics() values."""
        mock_thread_count = 5
        mock_memory_rss = 1024 * 1024 * 100  # 100 MB

        # Mock native.process_metrics() before construction too: _reset_state() (run at
        # enablement) now primes stored_cpu_times/stored_ctx_switches from a real reading,
        # so it must see a deterministic zero baseline rather than this test process's
        # actual, non-zero lifetime CPU time.
        with mock.patch(
            "ddtrace.internal.native.process_metrics",
            return_value=(0, 0, 0, 0, mock_thread_count, mock_memory_rss),
        ):
            collector = NativeProcessMetricCollector()
        native = collector.modules["ddtrace.internal.native"]
        # collector.__init__ already consumed a real time.monotonic() call for
        # _last_wall_time; seed it explicitly so the mocked "now" below produces a
        # deterministic 1s elapsed window.
        collector._last_wall_time = 0.0

        with (
            mock.patch.object(
                native,
                "process_metrics",
                # (cpu_time_sys_ns, cpu_time_user_ns, ctx_voluntary, ctx_involuntary, num_threads, rss_bytes)
                return_value=(0, 500_000_000, 1, 1, mock_thread_count, mock_memory_rss),
            ),
            mock.patch("ddtrace.internal.runtime.metric_collectors.time.monotonic", return_value=1.0),
        ):
            runtime_metrics = dict(collector.collect_fn(None))

        self.assertEqual(runtime_metrics[THREAD_COUNT], mock_thread_count)
        self.assertEqual(runtime_metrics[MEM_RSS], mock_memory_rss)
        # 0.5s of user cpu time over a 1s wall-clock window == 50%.
        self.assertEqual(runtime_metrics[CPU_PERCENT], 50.0)

    def test_negative_ctx_switches_are_omitted(self):
        """A negative ctx-switch value (platform can't report it) should not be fabricated as a delta."""
        collector = NativeProcessMetricCollector()
        native = collector.modules["ddtrace.internal.native"]

        with mock.patch.object(native, "process_metrics", return_value=(0, 0, -1, -1, 1, 0)):
            runtime_metrics = dict(collector.collect_fn(None))

        self.assertNotIn("runtime.python.cpu.ctx_switch.voluntary", runtime_metrics)
        self.assertNotIn("runtime.python.cpu.ctx_switch.involuntary", runtime_metrics)

    def test_process_metrics_failure_disables_collector(self):
        """If the smoke-test call to native.process_metrics() raises, the collector should
        disable itself and degrade gracefully, matching every other collector's
        required_modules ImportError contract.
        """
        with mock.patch("ddtrace.internal.native.process_metrics", side_effect=OSError("boom")):
            collector = NativeProcessMetricCollector()

        self.assertFalse(collector.enabled)
        # A disabled collector returns its (empty) `value` rather than `None`,
        # matching `TestRuntimeMetricCollector.test_failed_module_load_collect`'s contract.
        self.assertEqual(collector.collect(), [])


@pytest.mark.skipif(not hasattr(os, "fork"), reason="requires os.fork()")
@pytest.mark.subprocess(env={"PYTHONWARNINGS": "ignore::DeprecationWarning"})
def test_metrics_reflect_child_after_fork():
    """Regression test for https://github.com/DataDog/dd-trace-py/issues/19526:
    a collector constructed *before* fork (mirroring gunicorn's pre-fork enable())
    must still report the calling process's own state after fork, not the
    parent's -- because the native call takes no cached PID/handle, unlike the
    old psutil.Process(os.getpid()) object cached at construction time.

    Runs in an isolated, single-threaded subprocess (rather than forking the live,
    multithreaded pytest worker) to avoid the deadlock risk of os.fork() alongside
    other threads holding locks (e.g. the GC, logging, or malloc locks).

    Also confirms the fork-reset fix in NativeProcessMetricCollector._reset_state:
    the delta-tracked metrics (cpu times, ctx switches) must not go negative in the
    child just because it inherited the parent's last-observed values.
    """
    import os
    import time

    from ddtrace.internal.runtime.constants import CPU_TIME_SYS
    from ddtrace.internal.runtime.constants import CPU_TIME_USER
    from ddtrace.internal.runtime.constants import CTX_SWITCH_INVOLUNTARY
    from ddtrace.internal.runtime.constants import CTX_SWITCH_VOLUNTARY
    from ddtrace.internal.runtime.constants import MEM_RSS
    from ddtrace.internal.runtime.metric_collectors import NativeProcessMetricCollector

    collector = NativeProcessMetricCollector()

    # Establish a nonzero pre-fork baseline for the delta-tracked metrics. A forked
    # child's own /proc/self/stat (or platform equivalent) cpu-time counters start
    # near zero, so without the fork-reset fix the child would compute a negative
    # delta against this inherited (COW-copied) baseline -- with a zero baseline,
    # any delta is trivially non-negative and wouldn't catch a broken reset.
    end = time.monotonic() + 0.05
    while time.monotonic() < end:
        pass
    dict(collector.collect_fn(None))

    read_fd, write_fd = os.pipe()
    pid = os.fork()
    if pid == 0:
        os.close(read_fd)
        try:
            # Allocate enough memory that the child's RSS is unambiguously
            # larger than the parent's, then report metrics using the same
            # pre-fork collector instance.
            buf = bytearray(200 * 1024 * 1024)
            buf[:] = b"\x01" * len(buf)
            metrics = dict(collector.collect_fn(None))
            payload = "%d %d %d %d %d" % (
                metrics[MEM_RSS],
                metrics[CPU_TIME_SYS] >= 0,
                metrics[CPU_TIME_USER] >= 0,
                metrics.get(CTX_SWITCH_VOLUNTARY, 0) >= 0,
                metrics.get(CTX_SWITCH_INVOLUNTARY, 0) >= 0,
            )
            os.write(write_fd, payload.encode())
        finally:
            os._exit(0)
    else:
        os.close(write_fd)
        try:
            child_rss, cpu_sys_ok, cpu_user_ok, ctx_vol_ok, ctx_invol_ok = os.read(read_fd, 256).decode().split()
        finally:
            os.close(read_fd)
            os.waitpid(pid, 0)

        parent_rss = dict(collector.collect_fn(None))[MEM_RSS]

        assert int(child_rss) > parent_rss + 100 * 1024 * 1024
        assert cpu_sys_ok == "1"
        assert cpu_user_ok == "1"
        assert ctx_vol_ok == "1"
        assert ctx_invol_ok == "1"


class TestGCRuntimeMetricCollector(BaseTestCase):
    def test_metrics(self):
        collector = GCRuntimeMetricCollector()
        try:
            for metric_name, value in collector.collect(GC_RUNTIME_METRICS):
                self.assertIsNotNone(value)
                self.assertRegex(metric_name, r"^runtime.python\..*")
            self.assertEqual({name for name, _ in collector.collect(GC_RUNTIME_METRICS)}, GC_RUNTIME_METRICS)
        finally:
            collector.stop()

    def test_gen1_changes(self):
        # disable gc
        import gc

        gc.disable()
        collector = GCRuntimeMetricCollector()
        try:
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
        finally:
            collector.stop()
            gc.enable()

    def test_collections_and_pause_after_collect(self):
        import gc

        collector = GCRuntimeMetricCollector()
        try:
            dict(collector.collect(GC_RUNTIME_METRICS))
            gc.collect()
            metrics = dict(collector.collect(GC_RUNTIME_METRICS))
        finally:
            collector.stop()

        collections = metrics[GC_COLLECTIONS_GEN0] + metrics[GC_COLLECTIONS_GEN1] + metrics[GC_COLLECTIONS_GEN2]
        assert collections >= 1
        assert metrics[GC_PAUSE_TIME] > 0
        assert metrics[GC_PAUSE_MAX] > 0
        assert metrics[GC_PAUSE_MAX] <= metrics[GC_PAUSE_TIME]

    def test_pause_window_resets_between_flushes(self):
        collector = GCRuntimeMetricCollector()
        try:
            import gc

            gc.collect()
            dict(collector.collect(GC_RUNTIME_METRICS))
            metrics = dict(collector.collect(GC_RUNTIME_METRICS))
        finally:
            collector.stop()

        assert metrics[GC_PAUSE_TIME] == 0
        assert metrics[GC_PAUSE_MAX] == 0
        assert metrics[GC_COLLECTIONS_GEN0] == 0
        assert metrics[GC_COLLECTIONS_GEN1] == 0
        assert metrics[GC_COLLECTIONS_GEN2] == 0
