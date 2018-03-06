import os
import logging
import ddtrace

from .constants import DEFAULT_SERVICE

from ...ext import http
from ...util import asbool
from ...propagation.http import HTTPPropagator


log = logging.getLogger(__name__)


def _wrap_session_init(func, instance, args, kwargs):
    """Configure tracing settings when the `Session` is initialized"""
    func(*args, **kwargs)

    # set tracer settings
    distributed_tracing = asbool(os.environ.get('DATADOG_REQUESTS_DISTRIBUTED_TRACING')) or False
    service_name = os.environ.get('DATADOG_REQUESTS_SERVICE_NAME') or DEFAULT_SERVICE
    setattr(instance, 'distributed_tracing', distributed_tracing)
    setattr(instance, 'service_name', service_name)


def _extract_service_name(session, span):
    """Extracts the right service name based on the following logic:
    - `requests` is the default service name
    - users can change it via `session.service_name = 'clients'`
    - if the Span doesn't have a parent, use the set service name
      or fallback to the default
    - if the Span has a parent, use the set service name or the
      parent service value if the set service name is the default

    The priority can be represented as:
    Updated service name > parent service name > default to `requests`.
    """
    service_name = getattr(session, 'service_name', DEFAULT_SERVICE)
    if (service_name == DEFAULT_SERVICE and
            span._parent is not None and
            span._parent.service is not None):
        service_name = span._parent.service
    return service_name


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
        # update the span service name before doing any action
        span.service = _extract_service_name(instance, span)

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
