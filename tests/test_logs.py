import logging

from ddtrace.logs import TraceContextFilter

from unittest import TestCase
from nose.tools import eq_, ok_

from .util import override_global_tracer
from .test_tracer import get_dummy_tracer


class LogFilterTestCase(TestCase):
    """Test suite for Trace and Logs integration"""
    def setUp(self):
        # initializes a DummyTracer and store the `LogCapture` instance
        self.tracer = get_dummy_tracer()
        self.handler = logging.root.handlers[0]
        self.handler.setFormatter(logging.Formatter('dd.trace_id=%(trace_id)s dd.span_id=%(span_id)s %(message)s'))
        self.handler.setLevel(logging.INFO)
        self.handler.addFilter(TraceContextFilter())
        self.handler.truncate()
        self.log = logging.getLogger(__name__)

    def test_log_includes_correlation_identifiers(self):
        # ensures the right correlation identifiers are present in all log lines
        with override_global_tracer(self.tracer):
            span = self.tracer.trace('MockSpan')
            active_trace_id, active_span_id = span.trace_id, span.span_id
            self.log.info('Info notice')
            self.log.warn('Warning notice')
            self.log.error('Error notice')

        eq_(len(self.handler.buffer), 3)
        for record in self.handler.buffer:
            ok_(str(active_trace_id) in record)
            ok_(str(active_span_id) in record)

    def test_correlation_identifiers_without_trace(self):
        # empty Correlation Identifiers are present even if a Trace is not
        # currently active
        with override_global_tracer(self.tracer):
            self.log.info('Info notice')
            self.log.warn('Warning notice')
            self.log.error('Error notice')

        eq_(len(self.handler.buffer), 3)
        for record in self.handler.buffer:
            ok_('dd.trace_id=None' in record)
            ok_('dd.span_id=None' in record)
