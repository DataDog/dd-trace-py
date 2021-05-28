import mock

from tests.utils import TracerTestCase


class CorrelationIdentifiersTestCase(TracerTestCase):
    """Test suite for ``ddtrace.Tracer`` helper get_correlation_ids()"""

    def test_correlation_identifiers(self):
        # ensures the right correlation identifiers are
        # returned when a Trace is active
        span = self.tracer.trace("MockSpan")
        active_trace_id, active_span_id = span.trace_id, span.span_id
        trace_id, span_id = self.tracer.get_correlation_ids()

        self.assertEqual(trace_id, active_trace_id)
        self.assertEqual(span_id, active_span_id)

    def test_correlation_identifiers_without_trace(self):
        # ensures `None` is returned if no Traces are active
        trace_id, span_id = self.tracer.get_correlation_ids()

        self.assertIsNone(trace_id)
        self.assertIsNone(span_id)

    def test_correlation_identifiers_with_disabled_trace(self):
        # ensures `None` is returned if tracer is disabled
        self.tracer.enabled = False
        self.tracer.trace("MockSpan")
        trace_id, span_id = self.tracer.get_correlation_ids()

        self.assertIsNone(trace_id)
        self.assertIsNone(span_id)

    def test_correlation_identifiers_missing_context(self):
        # ensures we return `None` if there is no current context
        self.tracer.get_call_context = mock.MagicMock(return_value=None)

        trace_id, span_id = self.tracer.get_correlation_ids()

        self.assertIsNone(trace_id)
        self.assertIsNone(span_id)
