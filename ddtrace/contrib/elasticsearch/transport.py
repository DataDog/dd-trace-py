from elasticsearch import Transport

from .quantize import quantize
from . import metadata
from ...compat import json, urlencode

DEFAULT_SERVICE = 'elasticsearch'
SPAN_TYPE = 'elasticsearch'


def get_traced_transport(datadog_tracer, datadog_service=DEFAULT_SERVICE):

    class TracedTransport(Transport):
        """Extend elasticseach transport layer to allow Datadog tracer to catch any performed request"""

        _datadog_tracer = datadog_tracer
        _datadog_service = datadog_service

        def perform_request(self, method, url, params=None, body=None):
            """Wrap any request with a span

            We need to parse the URL to extract index/type/endpoints, but this catches all requests.
            This is ConnectionClass-agnostic.
            """
            with self._datadog_tracer.trace("elasticsearch.query") as s:
                # Don't instrument if the trace is not sampled
                if not s.sampled:
                    return super(TracedTransport, self).perform_request(method, url, params=params, body=body)

                s.service = self._datadog_service
                s.span_type = SPAN_TYPE
                s.set_tag(metadata.METHOD, method)
                s.set_tag(metadata.URL, url)
                s.set_tag(metadata.PARAMS, urlencode(params))
                if method == "GET":
                    s.set_tag(metadata.BODY, json.dumps(body))

                s = quantize(s)

                result = super(TracedTransport, self).perform_request(method, url, params=params, body=body)

                _, data = result
                took = data.get("took")
                if took:
                    # TODO: move that to a metric instead
                    s.set_tag(metadata.TOOK, took)

                return result

    return TracedTransport
