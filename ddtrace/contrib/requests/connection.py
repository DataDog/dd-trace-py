from typing import Optional

import ddtrace
from ddtrace import config

from .. import trace_utils
from ...constants import ANALYTICS_SAMPLE_RATE_KEY
from ...constants import SPAN_MEASURED_KEY
from ...ext import SpanTypes
from ...internal.logger import get_logger
from ...internal.utils import get_argument_value
from ...propagation.http import HTTPPropagator


log = get_logger(__name__)


def _extract_hostname(uri):
    # type: (str) -> str
    end = len(uri)
    j = uri.rfind("#", 0, end)
    if j != -1:
        end = j
    j = uri.rfind("&", 0, end)
    if j != -1:
        end = j

    start = uri.find("://", 0, end) + 3
    i = uri.find("@", start, end) + 1
    if i != 0:
        start = i
    j = uri.find("/", start, end)
    if j != -1:
        end = j

    return uri[start:end]


def _extract_query_string(uri):
    # type: (str) -> Optional[str]
    start = uri.find("?") + 1
    if start == 0:
        return None

    end = len(uri)
    j = uri.rfind("#", 0, end)
    if j != -1:
        end = j

    if end <= start:
        return None

    return uri[start:end]


def _wrap_send(func, instance, args, kwargs):
    """Trace the `Session.send` instance method"""
    # TODO[manu]: we already offer a way to provide the Global Tracer
    # and is ddtrace.tracer; it's used only inside our tests and can
    # be easily changed by providing a TracingTestCase that sets common
    # tracing functionalities.
    tracer = getattr(instance, "datadog_tracer", ddtrace.tracer)

    # skip if tracing is not enabled
    if not tracer.enabled:
        return func(*args, **kwargs)

    request = get_argument_value(args, kwargs, 0, "request")
    if not request:
        return func(*args, **kwargs)

    url = request.url
    hostname = _extract_hostname(url)

    cfg = config.get_from(instance)
    service = None
    if cfg["split_by_domain"] and hostname:
        service = hostname
    if service is None:
        service = cfg.get("service", None)
    if service is None:
        service = cfg.get("service_name", None)
    if service is None:
        service = trace_utils.ext_service(None, config.requests)

    with tracer.trace("requests.request", service=service, span_type=SpanTypes.HTTP) as span:
        span.set_tag(SPAN_MEASURED_KEY)

        # Configure trace search sample rate
        # DEV: analytics enabled on per-session basis
        cfg = config.get_from(instance)
        analytics_enabled = cfg.get("analytics_enabled")
        if analytics_enabled:
            span.set_tag(ANALYTICS_SAMPLE_RATE_KEY, cfg.get("analytics_sample_rate", True))

        # propagate distributed tracing headers
        if cfg.get("distributed_tracing"):
            HTTPPropagator.inject(span.context, request.headers)

        response = response_headers = None
        try:
            response = func(*args, **kwargs)
            return response
        finally:
            try:
                status = None
                if response is not None:
                    status = response.status_code
                    # Storing response headers in the span.
                    # Note that response.headers is not a dict, but an iterable
                    # requests custom structure, that we convert to a dict
                    response_headers = dict(getattr(response, "headers", {}))

                trace_utils.set_http_meta(
                    span,
                    config.requests,
                    request_headers=request.headers,
                    response_headers=response_headers,
                    method=request.method.upper(),
                    url=request.url,
                    status_code=status,
                    query=_extract_query_string(url),
                )
            except Exception:
                log.debug("requests: error adding tags", exc_info=True)
