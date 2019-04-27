from unittest import TestCase
from nose.tools import eq_
from tests.test_tracer import get_dummy_tracer

from ddtrace.propagation.http import (
    HTTPPropagator,
    HTTP_HEADER_TRACE_ID,
    HTTP_HEADER_PARENT_ID,
    HTTP_HEADER_SAMPLING_PRIORITY,
    HTTP_HEADER_ORIGIN,
)


class TestHttpPropagation(TestCase):
    """
    Tests related to the ``Context`` class that hosts the trace for the
    current execution flow.
    """

    def test_inject(self):
        tracer = get_dummy_tracer()

        with tracer.trace("global_root_span") as span:
            span.context.sampling_priority = 2
            span.context._dd_origin = "synthetics"
            headers = {}
            propagator = HTTPPropagator()
            propagator.inject(span.context, headers)

            eq_(int(headers[HTTP_HEADER_TRACE_ID]), span.trace_id)
            eq_(int(headers[HTTP_HEADER_PARENT_ID]), span.span_id)
            eq_(
                int(headers[HTTP_HEADER_SAMPLING_PRIORITY]),
                span.context.sampling_priority,
            )
            eq_(
                headers[HTTP_HEADER_ORIGIN],
                span.context._dd_origin,
            )

    def test_extract(self):
        tracer = get_dummy_tracer()

        headers = {
            "x-datadog-trace-id": "1234",
            "x-datadog-parent-id": "5678",
            "x-datadog-sampling-priority": "1",
            "x-datadog-origin": "synthetics",
        }

        propagator = HTTPPropagator()
        context = propagator.extract(headers)
        tracer.context_provider.activate(context)

        with tracer.trace("local_root_span") as span:
            eq_(span.trace_id, 1234)
            eq_(span.parent_id, 5678)
            eq_(span.context.sampling_priority, 1)
            eq_(span.context._dd_origin, "synthetics")

    def test_WSGI_extract(self):
        """Ensure we support the WSGI formatted headers as well."""
        tracer = get_dummy_tracer()

        headers = {
            "HTTP_X_DATADOG_TRACE_ID": "1234",
            "HTTP_X_DATADOG_PARENT_ID": "5678",
            "HTTP_X_DATADOG_SAMPLING_PRIORITY": "1",
            "HTTP_X_DATADOG_ORIGIN": "synthetics",
        }

        propagator = HTTPPropagator()
        context = propagator.extract(headers)
        tracer.context_provider.activate(context)

        with tracer.trace("local_root_span") as span:
            eq_(span.trace_id, 1234)
            eq_(span.parent_id, 5678)
            eq_(span.context.sampling_priority, 1)
            eq_(span.context._dd_origin, "synthetics")
