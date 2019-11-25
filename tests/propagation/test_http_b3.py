from unittest import TestCase
from tests.test_tracer import get_dummy_tracer

from ddtrace.ext.priority import AUTO_REJECT, AUTO_KEEP
from ddtrace.propagation.http import HTTPPropagator, set_http_propagator_factory
from ddtrace.propagation.b3 import B3HTTPPropagator


class TestB3HttpPropagation(TestCase):
    def test_b3_inject(self):
        tracer = get_dummy_tracer()
        tracer.configure(http_propagator=B3HTTPPropagator)

        with tracer.trace('global_root_span') as span:
            headers = {}
            set_http_propagator_factory(B3HTTPPropagator)
            propagator = HTTPPropagator()
            propagator.inject(span.context, headers)

            assert int(headers[B3HTTPPropagator.TRACE_ID_KEY], 16) == span.trace_id
            assert int(headers[B3HTTPPropagator.SPAN_ID_KEY], 16) == span.span_id
            assert int(headers[B3HTTPPropagator.SAMPLED_KEY]) == 1

    def test_b3_extract(self):
        tracer = get_dummy_tracer()
        tracer.configure(http_propagator=B3HTTPPropagator)

        headers = {
            'x-b3-traceid': '4d2',
            'x-b3-spanid': '162e',
            'x-b3-sampled': '1',
        }

        propagator = HTTPPropagator()
        context = propagator.extract(headers)
        tracer.context_provider.activate(context)

        with tracer.trace('local_root_span') as span:
            assert span.trace_id == 1234
            assert span.parent_id == 5678
            assert span.context.sampling_priority == AUTO_KEEP

        headers = {
            'x-b3-traceid': '4d2',
            'x-b3-spanid': '162e',
            'x-b3-sampled': '0',
        }

        propagator = HTTPPropagator()
        context = propagator.extract(headers)
        tracer.context_provider.activate(context)

        with tracer.trace('local_root_span') as span:
            assert span.context.sampling_priority == AUTO_REJECT
