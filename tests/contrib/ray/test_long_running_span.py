import time

from ddtrace import config
from ddtrace.contrib.internal.ray.span_manager import get_span_manager
from ddtrace.contrib.internal.ray.span_manager import start_long_running_span
from ddtrace.contrib.internal.ray.span_manager import stop_long_running_span
from tests.utils import TracerTestCase


_ray_span_manager = get_span_manager()


class TestLongRunningSpan(TracerTestCase):
    """Test long running span functionality without Ray dependencies"""

    def setUp(self):
        super().setUp()
        # Override timing values to make tests run quickly
        self.original_long_running_flush_interval = getattr(config, "_long_running_span_submission_interval", 120.0)
        self.original_long_running_initial_flush_interval = getattr(
            config, "_long_running_initial_flush_interval", 10.0
        )

        # Set fast timing for testing
        config._long_running_flush_interval = 2.0  # 2s instead of 120s
        config._long_running_initial_flush_interval = 1  # 1s instead of 10s

        # Clear any existing spans from the job manager
        with _ray_span_manager._lock:
            _ray_span_manager._job_spans.clear()
            _ray_span_manager._root_spans.clear()
            _ray_span_manager._timers.clear()

    def tearDown(self):
        # Restore original values
        config._long_running_flush_interval = self.original_long_running_flush_interval
        config._long_running_initial_flush_interval = self.original_long_running_initial_flush_interval

        # Clean up any remaining timers
        with _ray_span_manager._lock:
            for timer in _ray_span_manager._timers.values():
                timer.cancel()
            _ray_span_manager._job_spans.clear()
            _ray_span_manager._root_spans.clear()
            _ray_span_manager._timers.clear()
        super().tearDown()

    def test_long_running_span_basic_lifecycle(self):
        """Test basic lifecycle of a long running span"""

        span = self.tracer.start_span("test.long.running", service="test-service")
        span.set_tag_str("ray.submission_id", "test-submission-123")

        start_long_running_span(span)

        submission_id = "test-submission-123"
        with _ray_span_manager._lock:
            self.assertIn(submission_id, _ray_span_manager._job_spans)
            self.assertIn((span.trace_id, span.span_id), _ray_span_manager._job_spans[submission_id])

        time.sleep(1.5)

        self.assertGreater(span.get_metric("_dd.partial_version"), 0)
        self.assertEqual(span.get_tag("ray.job.status"), "RUNNING")

        stop_long_running_span(span)

        with _ray_span_manager._lock:
            job_spans = _ray_span_manager._job_spans.get(submission_id, {})
            self.assertNotIn((span.trace_id, span.span_id), job_spans)

        self.assertIsNone(span.get_metric("_dd.partial_version"))
        self.assertEqual(span.get_metric("_dd.was_long_running"), 1)
        self.assertTrue(span.finished)

    def test_not_long_running_span(self):
        """Test when a potential long running span lasts less then register_treshold"""
        span = self.tracer.start_span("test.not.long.running", service="test-service")
        span.set_tag_str("ray.submission_id", "test-submission-123")

        start_long_running_span(span)

        submission_id = "test-submission-123"
        with _ray_span_manager._lock:
            self.assertIn(submission_id, _ray_span_manager._job_spans)
            self.assertIn((span.trace_id, span.span_id), _ray_span_manager._job_spans[submission_id])

        self.assertIsNone(span.get_metric("_dd.partial_version"))
        self.assertIsNone(span.get_tag("ray.job.status"))

        stop_long_running_span(span)

        with _ray_span_manager._lock:
            job_spans = _ray_span_manager._job_spans.get(submission_id, {})
            self.assertNotIn((span.trace_id, span.span_id), job_spans)

        self.assertIsNone(span.get_metric("_dd.partial_version"))
        self.assertIsNone(span.get_metric("_dd.was_long_running"))
        self.assertTrue(span.finished)

    def test_multiple_long_running_spans_same_submission(self):
        """Test multiple spans with the same submission_id"""
        submission_id = "test-multi-submission-999"

        span1 = self.tracer.start_span("test.span1", service="test-service")
        span1.set_tag_str("ray.submission_id", submission_id)

        span2 = self.tracer.start_span("test.span2", service="test-service")
        span2.set_tag_str("ray.submission_id", submission_id)

        start_long_running_span(span1)
        start_long_running_span(span2)

        with _ray_span_manager._lock:
            job_spans = _ray_span_manager._job_spans[submission_id]
            self.assertIn((span1.trace_id, span1.span_id), job_spans)
            self.assertIn((span2.trace_id, span2.span_id), job_spans)

        time.sleep(2)

        self.assertGreater(span1.get_metric("_dd.partial_version"), 0)
        self.assertEqual(span1.get_tag("ray.job.status"), "RUNNING")

        self.assertGreater(span2.get_metric("_dd.partial_version"), 0)
        self.assertEqual(span2.get_tag("ray.job.status"), "RUNNING")

        stop_long_running_span(span1)
        stop_long_running_span(span2)

        self.assertTrue(span1.finished)
        self.assertTrue(span2.finished)
        self.assertEqual(span1.get_metric("_dd.was_long_running"), 1)
        self.assertEqual(span2.get_metric("_dd.was_long_running"), 1)

    def test_long_running_span_hierarchies_and_context(self):
        """Test parent/child relationships with mixed long-running and non-long-running spans."""
        submission_id = "test-parent-child-hierarchy-456"
        parent_span = self.tracer.start_span(name="test.long.parent", service="test-service")
        parent_span.set_tag_str("ray.submission_id", submission_id)
        start_long_running_span(parent_span)

        child1 = self.tracer.start_span(name="test.child1.long", service="test-service", child_of=parent_span)
        child1.set_tag_str("ray.submission_id", submission_id)
        start_long_running_span(child1)

        time.sleep(3)
        stop_long_running_span(child1)
        self.assertTrue(child1.finished)
        self.assertEqual(child1.get_metric("_dd.was_long_running"), 1)

        child2 = self.tracer.start_span(name="test.child2.short", service="test-service", child_of=parent_span)
        child2.finish()
        self.assertTrue(child2.finished)
        self.assertIsNone(child2.get_metric("_dd.partial_version"))

        child3 = self.tracer.start_span(name="test.child3.long", service="test-service", child_of=parent_span)
        child3.set_tag_str("ray.submission_id", submission_id)
        start_long_running_span(child3)

        with _ray_span_manager._lock:
            job_spans = _ray_span_manager._job_spans[submission_id]
            self.assertIn((parent_span.trace_id, parent_span.span_id), job_spans)
            self.assertIn((child3.trace_id, child3.span_id), job_spans)
            self.assertNotIn((child1.trace_id, child1.span_id), job_spans)
            self.assertNotIn((child2.trace_id, child2.span_id), job_spans)

        time.sleep(1.5)

        self.assertGreater(parent_span.get_metric("_dd.partial_version"), 0)
        self.assertEqual(parent_span.get_tag("ray.job.status"), "RUNNING")
        self.assertGreater(child3.get_metric("_dd.partial_version"), 0)
        self.assertEqual(child3.get_tag("ray.job.status"), "RUNNING")

        stop_long_running_span(child3)
        stop_long_running_span(parent_span)

        self.assertTrue(parent_span.finished)
        self.assertTrue(child3.finished)
        self.assertEqual(parent_span.get_metric("_dd.was_long_running"), 1)
        self.assertEqual(child3.get_metric("_dd.was_long_running"), 1)
        self.assertEqual(child1.parent_id, parent_span.span_id)
        self.assertEqual(child2.parent_id, parent_span.span_id)
        self.assertEqual(child3.parent_id, parent_span.span_id)
