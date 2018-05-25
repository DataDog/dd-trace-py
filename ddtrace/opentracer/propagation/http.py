import logging

from ddtrace.propagation.http import HTTPPropagator as DDHTTPPropagator

from ..span_context import SpanContext

from .propagator import Propagator


log = logging.getLogger(__name__)

HTTP_BAGGAGE_PREFIX = 'ot-baggage-'
HTTP_BAGGAGE_PREFIX_LEN = len(HTTP_BAGGAGE_PREFIX)


class HTTPPropagator(Propagator):
    slots = ['_dd_propagator']

    def __init__(self):
        self._dd_propagator = DDHTTPPropagator()

    def inject(self, span_context, carrier):
        """ """
        self._dd_propagator.inject(span_context._get_dd_context(), carrier)

        # Add the baggage
        if span_context.baggage is not None:
            for key in span_context.baggage:
                carrier[HTTP_BAGGAGE_PREFIX + key] = span_context.baggage[key]

    def extract(self, carrier):
        ddspan_ctx = self._dd_propagator.extract(carrier)

        baggage = {}
        for key in carrier:
            if key.startswith(HTTP_BAGGAGE_PREFIX):
                baggage[key[HTTP_BAGGAGE_PREFIX_LEN:]] = carrier[key]
            # elif key is not in [HTTP_HEADER..., ]
            # raise SpanContextCorruptedException

        return SpanContext(context=ddspan_ctx, baggage=baggage)
