import os
import sys

import six

from ddtrace import config
from ddtrace.vendor import wrapt

from .. import trace_utils
from ...constants import ANALYTICS_SAMPLE_RATE_KEY
from ...ext import SpanTypes
from ...internal.compat import PY2
from ...internal.compat import httplib
from ...internal.compat import parse
from ...internal.logger import get_logger
from ...internal.utils.formats import asbool
from ...pin import Pin
from ...propagation.http import HTTPPropagator
from ..trace_utils import unwrap as _u


span_name = "httplib.request" if PY2 else "http.client.request"

log = get_logger(__name__)


config._add(
    "httplib",
    {
        "distributed_tracing": asbool(os.getenv("DD_HTTPLIB_DISTRIBUTED_TRACING", default=True)),
    },
)


def _wrap_init(func, instance, args, kwargs):
    Pin(service=None, _config=config.httplib).onto(instance)
    return func(*args, **kwargs)


def _wrap_getresponse(func, instance, args, kwargs):
    # Use any attached tracer if available, otherwise use the global tracer
    pin = Pin.get_from(instance)
    if not pin or not pin.enabled():
        return func(*args, **kwargs)

    resp = None
    try:
        resp = func(*args, **kwargs)
        return resp
    finally:
        try:
            # Get the span attached to this instance, if available
            span = getattr(instance, "_datadog_span", None)
            if span:
                if resp:
                    trace_utils.set_http_meta(
                        span, config.httplib, status_code=resp.status, response_headers=resp.getheaders()
                    )

                span.finish()
                delattr(instance, "_datadog_span")
        except Exception:
            log.debug("error applying request tags", exc_info=True)


def _wrap_request(func, instance, args, kwargs):
    # Use any attached tracer if available, otherwise use the global tracer
    pin = Pin.get_from(instance)
    if should_skip_request(pin, instance):
        return func(*args, **kwargs)

    cfg = config.get_from(instance)

    try:
        # Create a new span and attach to this instance (so we can retrieve/update/close later on the response)
        span = pin.tracer.trace(span_name, span_type=SpanTypes.HTTP)
        setattr(instance, "_datadog_span", span)

        # propagate distributed tracing headers
        if cfg.get("distributed_tracing"):
            if len(args) > 3:
                headers = args[3]
            else:
                headers = kwargs.setdefault("headers", {})
            HTTPPropagator.inject(span.context, headers)
    except Exception:
        log.debug("error configuring request", exc_info=True)
        span = getattr(instance, "_datadog_span", None)
        if span:
            span.finish()

    try:
        return func(*args, **kwargs)
    except Exception:
        span = getattr(instance, "_datadog_span", None)
        exc_info = sys.exc_info()
        if span:
            span.set_exc_info(*exc_info)
            span.finish()
        six.reraise(*exc_info)


def _wrap_putrequest(func, instance, args, kwargs):
    # Use any attached tracer if available, otherwise use the global tracer
    pin = Pin.get_from(instance)
    if should_skip_request(pin, instance):
        return func(*args, **kwargs)

    try:
        if hasattr(instance, "_datadog_span"):
            # Reuse an existing span set in _wrap_request
            span = instance._datadog_span
        else:
            # Create a new span and attach to this instance (so we can retrieve/update/close later on the response)
            span = pin.tracer.trace(span_name, span_type=SpanTypes.HTTP)
            setattr(instance, "_datadog_span", span)

        method, path = args[:2]
        scheme = "https" if isinstance(instance, httplib.HTTPSConnection) else "http"
        port = ":{port}".format(port=instance.port)

        if (scheme == "http" and instance.port == 80) or (scheme == "https" and instance.port == 443):
            port = ""
        url = "{scheme}://{host}{port}{path}".format(scheme=scheme, host=instance.host, port=port, path=path)

        # sanitize url
        parsed = parse.urlparse(url)
        sanitized_url = parse.urlunparse(
            (parsed.scheme, parsed.netloc, parsed.path, parsed.params, None, parsed.fragment)  # drop query
        )
        trace_utils.set_http_meta(span, config.httplib, method=method, url=sanitized_url, query=parsed.query)

        # set analytics sample rate
        span.set_tag(ANALYTICS_SAMPLE_RATE_KEY, config.httplib.get_analytics_sample_rate())
    except Exception:
        log.debug("error applying request tags", exc_info=True)

        # Close the span to prevent memory leaks.
        span = getattr(instance, "_datadog_span", None)
        if span:
            span.finish()

    try:
        return func(*args, **kwargs)
    except Exception:
        span = getattr(instance, "_datadog_span", None)
        exc_info = sys.exc_info()
        if span:
            span.set_exc_info(*exc_info)
            span.finish()
        six.reraise(*exc_info)


def _wrap_putheader(func, instance, args, kwargs):
    span = getattr(instance, "_datadog_span", None)
    if span:
        request_headers = {args[0]: args[1]}
        trace_utils.set_http_meta(span, config.httplib, request_headers=request_headers)

    return func(*args, **kwargs)


def should_skip_request(pin, request):
    """Helper to determine if the provided request should be traced"""
    if not pin or not pin.enabled():
        return True

    agent_url = pin.tracer.agent_trace_url
    if agent_url:
        parsed = parse.urlparse(agent_url)
        return request.host == parsed.hostname and request.port == parsed.port
    return False


def patch():
    """patch the built-in urllib/httplib/httplib.client methods for tracing"""
    if getattr(httplib, "__datadog_patch", False):
        return
    setattr(httplib, "__datadog_patch", True)

    # Patch the desired methods
    setattr(httplib.HTTPConnection, "__init__", wrapt.FunctionWrapper(httplib.HTTPConnection.__init__, _wrap_init))
    setattr(
        httplib.HTTPConnection,
        "getresponse",
        wrapt.FunctionWrapper(httplib.HTTPConnection.getresponse, _wrap_getresponse),
    )
    setattr(httplib.HTTPConnection, "request", wrapt.FunctionWrapper(httplib.HTTPConnection.request, _wrap_request))
    setattr(
        httplib.HTTPConnection, "putrequest", wrapt.FunctionWrapper(httplib.HTTPConnection.putrequest, _wrap_putrequest)
    )
    setattr(
        httplib.HTTPConnection, "putheader", wrapt.FunctionWrapper(httplib.HTTPConnection.putheader, _wrap_putheader)
    )


def unpatch():
    """unpatch any previously patched modules"""
    if not getattr(httplib, "__datadog_patch", False):
        return
    setattr(httplib, "__datadog_patch", False)

    _u(httplib.HTTPConnection, "__init__")
    _u(httplib.HTTPConnection, "getresponse")
    _u(httplib.HTTPConnection, "request")
    _u(httplib.HTTPConnection, "putrequest")
    _u(httplib.HTTPConnection, "putheader")
