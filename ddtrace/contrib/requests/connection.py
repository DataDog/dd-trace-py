import os
import logging
import ddtrace

from ...ext import http
from ...util import asbool
from ...propagation.http import HTTPPropagator


log = logging.getLogger(__name__)


def _wrap_session_init(func, instance, args, kwargs):
    """Configure tracing settings when the `Session` is initialized"""
    func(*args, **kwargs)

    # set tracer settings
    distributed_tracing = asbool(os.environ.get('DATADOG_REQUESTS_DISTRIBUTED_TRACING')) or False
    setattr(instance, 'distributed_tracing', distributed_tracing)


def _wrap_request(func, instance, args, kwargs):
    """Trace the `Session.request` instance method"""
    tracer = getattr(instance, 'datadog_tracer', ddtrace.tracer)

    # [TODO:christian] replace this with a unified way of handling options (eg, Pin)
    distributed_tracing = getattr(instance, 'distributed_tracing', None)

    # skip if tracing is not enabled
    if not tracer.enabled:
        return func(*args, **kwargs)

    method = kwargs.get('method') or args[0]
    url = kwargs.get('url') or args[1]
    headers = kwargs.get('headers', {})

    with tracer.trace("requests.request", span_type=http.TYPE) as span:
        if distributed_tracing:
            propagator = HTTPPropagator()
            propagator.inject(span.context, headers)
            kwargs['headers'] = headers

        response = None
        try:
            response = func(*args, **kwargs)
            return response
        finally:
            try:
                span.set_tag(http.METHOD, method)
                span.set_tag(http.URL, url)
                if response is not None:
                    span.set_tag(http.STATUS_CODE, response.status_code)
                    # `span.error` must be an integer
                    span.error = int(500 <= response.status_code)
            except Exception:
                log.debug("error patching tags", exc_info=True)
