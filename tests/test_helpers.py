from ddtrace import helpers

from unittest import TestCase
from nose.tools import eq_, ok_

from .util import override_global_tracer
from .test_tracer import get_dummy_tracer

class HelpersTestCase(TestCase):
    """Test suite for ``ddtrace`` helpers"""
    def setUp(self):
        # initializes a DummyTracer
        self.tracer = get_dummy_tracer()

    def test_correlation_identifiers(self):
        # ensures the right correlation identifiers are
        # returned when a Trace is active
        with override_global_tracer(self.tracer):
            span = self.tracer.trace('MockSpan')
            active_trace_id, active_span_id = span.trace_id, span.span_id
            trace_id, span_id = helpers.get_correlation_ids()

        eq_(trace_id, active_trace_id)
        eq_(span_id, active_span_id)

    def test_correlation_identifiers_without_trace(self):
        # ensures `None` is returned if no Traces are active
        with override_global_tracer(self.tracer):
            trace_id, span_id = helpers.get_correlation_ids()

        ok_(trace_id is None)
        ok_(span_id is None)
