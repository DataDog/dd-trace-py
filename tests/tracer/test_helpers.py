import mock

from ddtrace import helpers
from ddtrace.vendor.debtcollector.removals import removed_class
from tests.utils import TracerTestCase
from tests.utils import override_global_tracer


@removed_class(
    "HelpersTestCase",
    message="ddtrace.helpers has been deprecated and will no longer require HelpersTestCase",
    version="1.0.0",
)
class HelpersTestCase(TracerTestCase):
    """Test suite for ``ddtrace`` helpers"""

    def test_correlation_identifiers(self):
        # ensures the right correlation identifiers are
        # returned when a Trace is active
        with override_global_tracer(self.tracer):
            span = self.tracer.trace("MockSpan")
            active_trace_id, active_span_id = span.trace_id, span.span_id
            trace_id, span_id = helpers.get_correlation_ids()

        self.assertEqual(trace_id, active_trace_id)
        self.assertEqual(span_id, active_span_id)

    def test_correlation_identifiers_without_trace(self):
        # ensures `None` is returned if no Traces are active
        with override_global_tracer(self.tracer):
            trace_id, span_id = helpers.get_correlation_ids()

        self.assertIsNone(trace_id)
        self.assertIsNone(span_id)

    def test_correlation_identifiers_with_disabled_trace(self):
        # ensures `None` is returned if tracer is disabled
        with override_global_tracer(self.tracer):
            self.tracer.enabled = False
            self.tracer.trace("MockSpan")
            trace_id, span_id = helpers.get_correlation_ids()

        self.assertIsNone(trace_id)
        self.assertIsNone(span_id)

    def test_correlation_identifiers_missing_context(self):
        # ensures we return `None` if there is no current context
        self.tracer.current_trace_context = mock.MagicMock(return_value=None)

        with override_global_tracer(self.tracer):
            trace_id, span_id = helpers.get_correlation_ids()

        self.assertIsNone(trace_id)
        self.assertIsNone(span_id)
