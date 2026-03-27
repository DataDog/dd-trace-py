import time

from ddtrace.constants import ERROR_MSG
from ddtrace.constants import ERROR_TYPE
from ddtrace.constants import PARTIAL_VERSION
from ddtrace.constants import WAS_LONG_RUNNING
from tests.subprocesstest import run_in_subprocess
from tests.utils import TracerTestCase


class TestRunningSpan(TracerTestCase):
    @run_in_subprocess(env_overrides=dict(DD_TRACE_EXPERIMENTAL_LONG_RUNNING_FLUSH_INTERVAL="0.5"))
    def test_running_span_basic_lifecycle(self):
        """Test running spans emit partial flushes before final finish."""
        from ddtrace._trace.running_span import start_running_span
        from ddtrace._trace.running_span import stop_running_span

        span = start_running_span("test.long.running", service="test-service")
        span._set_attribute("foo", "bar")

        # Wait long enough to observe repeated partial flushes.
        time.sleep(1.1)

        partial_traces = self.pop_traces()
        assert len(partial_traces) >= 2
        partial_versions = []
        for trace in partial_traces:
            assert len(trace) == 1
            emitted = trace[0]
            self.assertEqual(emitted.name, "test.long.running")
            self.assertEqual(emitted.service, "test-service")
            self.assertEqual(emitted.get_tag("foo"), "bar")
            partial_version = emitted.get_metric(PARTIAL_VERSION)
            assert partial_version is not None
            assert partial_version > 0
            partial_versions.append(partial_version)
            # check partial span is finished
            self.assertIsNotNone(emitted.duration)

        # check each partial version is different
        self.assertEqual(len(partial_versions), len(set(partial_versions)))

        stop_running_span(span)

        final_traces = self.pop_traces()
        assert len(final_traces) == 1
        assert len(final_traces[0]) == 1
        final_span = final_traces[0][0]
        self.assertIsNone(final_span.get_metric(PARTIAL_VERSION))
        self.assertEqual(final_span.get_metric(WAS_LONG_RUNNING), 1)

    @run_in_subprocess(env_overrides=dict(DD_TRACE_EXPERIMENTAL_LONG_RUNNING_FLUSH_INTERVAL="0.5"))
    def test_running_span_context_manager(self):
        """Test running spans work with context manager and emit partial flushes."""
        from ddtrace._trace.running_span import running_span

        with running_span("test.long.running", service="test-service") as span:
            span._set_attribute("foo", "bar")

            # Wait long enough to observe repeated partial flushes.
            time.sleep(1.1)

            partial_traces = self.pop_traces()
            assert len(partial_traces) >= 2
            for trace in partial_traces:
                assert len(trace) == 1
                emitted = trace[0]
                self.assertEqual(emitted.name, "test.long.running")
                self.assertEqual(emitted.service, "test-service")
                self.assertEqual(emitted.get_tag("foo"), "bar")
                partial_version = emitted.get_metric(PARTIAL_VERSION)
                assert partial_version is not None
                assert partial_version > 0

        final_traces = self.pop_traces()
        assert len(final_traces) == 1
        assert len(final_traces[0]) == 1
        final_span = final_traces[0][0]
        self.assertIsNone(final_span.get_metric(PARTIAL_VERSION))
        self.assertEqual(final_span.get_metric(WAS_LONG_RUNNING), 1)

    @run_in_subprocess(env_overrides=dict(DD_TRACE_EXPERIMENTAL_LONG_RUNNING_FLUSH_INTERVAL="0.5"))
    def test_stop_before_first_interval_emits_only_final_span(self):
        """Test that stopping a span before the first flush interval emits only the final span."""
        from ddtrace._trace.running_span import start_running_span
        from ddtrace._trace.running_span import stop_running_span

        span = start_running_span("test.short.running", service="test-service")
        stop_running_span(span)

        traces = self.pop_traces()
        assert len(traces) == 1
        assert len(traces[0]) == 1
        emitted = traces[0][0]
        self.assertEqual(emitted.name, "test.short.running")
        self.assertIsNone(emitted.get_metric(PARTIAL_VERSION))
        self.assertIsNone(emitted.get_metric(WAS_LONG_RUNNING))

    @run_in_subprocess(env_overrides=dict(DD_TRACE_EXPERIMENTAL_LONG_RUNNING_FLUSH_INTERVAL="0.5"))
    def test_context_manager_records_exception_and_finishes(self):
        """Test that context manager records exceptions and finishes the span properly."""
        from ddtrace._trace.running_span import running_span

        with self.assertRaises(ValueError):
            with running_span("test.running.error", service="test-service"):
                raise ValueError("boom")

        traces = self.pop_traces()
        assert len(traces) == 1
        assert len(traces[0]) == 1
        emitted = traces[0][0]
        self.assertEqual(emitted.name, "test.running.error")
        self.assertEqual(emitted.error, 1)
        self.assertEqual(emitted.get_tag(ERROR_MSG), "boom")
        self.assertEqual(emitted.get_tag(ERROR_TYPE), "builtins.ValueError")
        self.assertIsNotNone(emitted.duration)
        self.assertIsNone(emitted.get_metric(PARTIAL_VERSION))

    @run_in_subprocess(env_overrides=dict(DD_TRACE_EXPERIMENTAL_LONG_RUNNING_FLUSH_INTERVAL="0.5"))
    def test_multiple_running_spans_timer_lifecycle(self):
        """Test that multiple running spans are tracked independently and timer stops when all spans finish."""
        from ddtrace._trace.running_span import get_running_span_manager
        from ddtrace._trace.running_span import start_running_span
        from ddtrace._trace.running_span import stop_running_span

        span1 = start_running_span("test.running.one", service="test-service")
        span2 = start_running_span("test.running.two", service="test-service")

        time.sleep(0.7)
        traces = self.pop_traces()
        names = {trace[0].name for trace in traces if len(trace) == 1}
        assert "test.running.one" in names
        assert "test.running.two" in names

        stop_running_span(span1)
        stop_traces = self.pop_traces()
        assert any(
            len(trace) == 1
            and trace[0].name == "test.running.one"
            and trace[0].get_metric(PARTIAL_VERSION) is None
            and trace[0].get_metric(WAS_LONG_RUNNING) == 1
            for trace in stop_traces
        )

        time.sleep(0.7)
        later_traces = self.pop_traces()
        assert later_traces
        assert all(len(trace) == 1 and trace[0].name == "test.running.two" for trace in later_traces)
        assert all(trace[0].get_metric(PARTIAL_VERSION) is not None for trace in later_traces)

        stop_running_span(span2)
        self.pop_traces()
        self.assertIsNone(get_running_span_manager()._timer)

        time.sleep(0.7)
        assert self.pop_traces() == []

    @run_in_subprocess(env_overrides=dict(DD_TRACE_EXPERIMENTAL_LONG_RUNNING_FLUSH_INTERVAL="0.5"))
    def test_partial_flush_includes_finished_children(self):
        """Test that partial flushes include child spans that have finished."""
        from ddtrace._trace.running_span import running_span

        with running_span("test.running.root", service="test-service", activate=True):
            with self.tracer.trace("test.running.child"):
                pass

            time.sleep(0.7)
            partial_traces = self.pop_traces()
            assert any(
                len(trace) == 2 and {span.name for span in trace} == {"test.running.root", "test.running.child"}
                for trace in partial_traces
            )

        final_traces = self.pop_traces()
        assert len(final_traces) == 1
        # check the finish child span is not re-sent
        assert len(final_traces[0]) == 1
        final_span = final_traces[0][0]
        # check common running span behavior
        self.assertEqual(final_span.name, "test.running.root")
        self.assertIsNone(final_span.get_metric(PARTIAL_VERSION))
        self.assertEqual(final_span.get_metric(WAS_LONG_RUNNING), 1)

    @run_in_subprocess(env_overrides=dict(DD_TRACE_EXPERIMENTAL_LONG_RUNNING_FLUSH_INTERVAL="0.5"))
    def test_unstopped_running_span_flushes_on_process_exit(self):
        """Test that unstopped running spans are flushed properly on process exit."""
        from ddtrace._trace.running_span import get_running_span_manager
        from ddtrace._trace.running_span import start_running_span

        start_running_span("test.running.exit", service="test-service")
        time.sleep(0.7)
        partial_traces = self.pop_traces()
        assert partial_traces
        assert all(trace[0].get_metric(PARTIAL_VERSION) is not None for trace in partial_traces)

        get_running_span_manager()._cleanup_on_exit()

        final_traces = self.pop_traces()
        assert len(final_traces) == 1
        assert len(final_traces[0]) == 1
        final_span = final_traces[0][0]
        self.assertEqual(final_span.name, "test.running.exit")
        self.assertIsNone(final_span.get_metric(PARTIAL_VERSION))
        self.assertEqual(final_span.get_metric(WAS_LONG_RUNNING), 1)
