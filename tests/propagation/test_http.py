from unittest import TestCase
from nose.tools import eq_, ok_
from tests.test_tracer import get_dummy_tracer

from ddtrace.span import Span
from ddtrace.context import Context, ThreadLocalContext

from ddtrace.propagation.http import (
    HTTPPropagator,
    HTTP_HEADER_TRACE_ID,
    HTTP_HEADER_PARENT_ID,
    HTTP_HEADER_SAMPLING_PRIORITY,
)
from ddtrace.propagation.utils import get_wsgi_header

class TestHttpPropagation(TestCase):
    """
    Tests related to the ``Context`` class that hosts the trace for the
    current execution flow.
    """
    def test_inject(self):
        tracer = get_dummy_tracer()

        with tracer.trace("global_root_span") as span:
            headers = {}
            propagator = HTTPPropagator()
            propagator.inject(span.context, headers)

            eq_(int(headers[HTTP_HEADER_TRACE_ID]), span.trace_id)
            eq_(int(headers[HTTP_HEADER_PARENT_ID]), span.span_id)
            # TODO: do it for priority too


    def test_extract(self):
        tracer = get_dummy_tracer()

        headers = {
            HTTP_HEADER_TRACE_ID: '1234',
            HTTP_HEADER_PARENT_ID: '5678',
            HTTP_HEADER_SAMPLING_PRIORITY: '1',
        }

        propagator = HTTPPropagator()
        context = propagator.extract(headers)
        tracer.context_provider.activate(context)

        with tracer.trace("local_root_span") as span:
            eq_(span.trace_id, 1234)
            eq_(span.parent_id, 5678)
            # TODO: do it for priority too

    def test_WSGI_extract(self):
        """Ensure we support the WSGI formatted headers as well."""
        tracer = get_dummy_tracer()

        headers = {
            get_wsgi_header(HTTP_HEADER_TRACE_ID): '1234',
            get_wsgi_header(HTTP_HEADER_PARENT_ID): '5678',
            get_wsgi_header(HTTP_HEADER_SAMPLING_PRIORITY): '1',
        }

        propagator = HTTPPropagator()
        context = propagator.extract(headers)
        tracer.context_provider.activate(context)

        with tracer.trace("local_root_span") as span:
            eq_(span.trace_id, 1234)
            eq_(span.parent_id, 5678)
            # TODO: do it for priority too
