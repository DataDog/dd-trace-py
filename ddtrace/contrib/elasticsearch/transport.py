try:
    from elasticsearch import Transport
except ImportError:
    Transport = object

from .quantize import quantize
from . import metadata

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
                s.service = self._datadog_service
                s.span_type = SPAN_TYPE
                s.set_tag(metadata.METHOD, method)
                s.set_tag(metadata.URL, url)
                s = quantize(s)

                try:
                    result = super(TracedTransport, self).perform_request(method, url, params=params, body=body)
                    return result
                finally:
                    _, data = result
                    took = data.get("took")
                    if took:
                        # TODO: move that to a metric instead
                        s.set_tag(metadata.TOOK, took)

    return TracedTransport
