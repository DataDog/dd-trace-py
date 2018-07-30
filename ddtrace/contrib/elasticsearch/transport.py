from elasticsearch import Transport
from elasticsearch.exceptions import TransportError

from . import metadata
from .quantize import quantize

from ...utils.deprecation import deprecated
from ...compat import urlencode
from ...ext import AppTypes, http

DEFAULT_SERVICE = 'elasticsearch'
SPAN_TYPE = 'elasticsearch'


@deprecated(message='Use patching instead (see the docs).', version='1.0.0')
def get_traced_transport(datadog_tracer, datadog_service=DEFAULT_SERVICE):

    datadog_tracer.set_service_info(
        service=datadog_service,
        app=SPAN_TYPE,
        app_type=AppTypes.db,
    )

    class TracedTransport(Transport):
        """ Extend elasticseach transport layer to allow Datadog
            tracer to catch any performed request.
        """

        _datadog_tracer = datadog_tracer
        _datadog_service = datadog_service

        def perform_request(self, method, url, params=None, body=None):
            with self._datadog_tracer.trace("elasticsearch.query") as s:
                # Don't instrument if the trace is not sampled
                if not s.sampled:
                    return super(TracedTransport, self).perform_request(
                        method, url, params=params, body=body)

                s.service = self._datadog_service
                s.span_type = SPAN_TYPE
                s.set_tag(metadata.METHOD, method)
                s.set_tag(metadata.URL, url)
                s.set_tag(metadata.PARAMS, urlencode(params))
                if method == "GET":
                    s.set_tag(metadata.BODY, self.serializer.dumps(body))
                s = quantize(s)

                try:
                    result = super(TracedTransport, self).perform_request(method, url, params=params, body=body)
                except TransportError as e:
                    s.set_tag(http.STATUS_CODE, e.status_code)
                    raise

                status = None
                if isinstance(result, tuple) and len(result) == 2:
                    # elasticsearch<2.4; it returns both the status and the body
                    status, data = result
                else:
                    # elasticsearch>=2.4; internal change for ``Transport.perform_request``
                    # that just returns the body
                    data = result

                if status:
                    s.set_tag(http.STATUS_CODE, status)

                took = data.get("took")
                if took:
                    s.set_metric(metadata.TOOK, int(took))

                return result
    return TracedTransport
