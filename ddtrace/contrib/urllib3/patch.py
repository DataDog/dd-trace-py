import os

import urllib3

from ddtrace import config
from ddtrace.pin import Pin
from ddtrace.vendor.wrapt import wrap_function_wrapper as _w

from .. import trace_utils
from ...constants import ANALYTICS_SAMPLE_RATE_KEY
from ...ext import SpanTypes
from ...internal.compat import parse
from ...internal.utils import ArgumentError
from ...internal.utils import get_argument_value
from ...internal.utils.formats import asbool
from ...internal.utils.wrappers import unwrap as _u
from ...propagation.http import HTTPPropagator


# Ports which, if set, will not be used in hostnames/service names
DROP_PORTS = (80, 443)

# Initialize the default config vars
config._add(
    "urllib3",
    {
        "_default_service": "urllib3",
        "distributed_tracing": asbool(os.getenv("DD_URLLIB3_DISTRIBUTED_TRACING", default=True)),
        "split_by_domain": asbool(os.getenv("DD_URLLIB3_SPLIT_BY_DOMAIN", default=False)),
    },
)


def patch():
    """Enable tracing for all urllib3 requests"""
    if getattr(urllib3, "__datadog_patch", False):
        return
    setattr(urllib3, "__datadog_patch", True)

    _w("urllib3", "connectionpool.HTTPConnectionPool.urlopen", _wrap_urlopen)
    Pin().onto(urllib3.connectionpool.HTTPConnectionPool)


def unpatch():
    """Disable trace for all urllib3 requests"""
    if getattr(urllib3, "__datadog_patch", False):
        setattr(urllib3, "__datadog_patch", False)

        _u(urllib3.connectionpool.HTTPConnectionPool, "urlopen")


def _wrap_urlopen(func, instance, args, kwargs):
    """
    Wrapper function for the lower-level urlopen in urllib3

    :param func: The original target function "urlopen"
    :param instance: The patched instance of ``HTTPConnectionPool``
    :param args: Positional arguments from the target function
    :param kwargs: Keyword arguments from the target function
    :return: The ``HTTPResponse`` from the target function
    """
    request_method = get_argument_value(args, kwargs, 0, "method")
    request_url = get_argument_value(args, kwargs, 1, "url")
    try:
        request_headers = get_argument_value(args, kwargs, 3, "headers")
    except ArgumentError:
        request_headers = None
    try:
        request_retries = get_argument_value(args, kwargs, 4, "retries")
    except ArgumentError:
        request_retries = None

    # HTTPConnectionPool allows relative path requests; convert the request_url to an absolute url
    if request_url.startswith("/"):
        request_url = parse.urlunparse(
            (
                instance.scheme,
                "{}:{}".format(instance.host, instance.port)
                if instance.port and instance.port not in DROP_PORTS
                else str(instance.host),
                request_url,
                None,
                None,
                None,
            )
        )

    parsed_uri = parse.urlparse(request_url)
    hostname = parsed_uri.netloc

    pin = Pin.get_from(instance)
    if not pin or not pin.enabled():
        return func(*args, **kwargs)

    with pin.tracer.trace(
        "urllib3.request", service=trace_utils.ext_service(pin, config.urllib3), span_type=SpanTypes.HTTP
    ) as span:
        if config.urllib3.split_by_domain:
            span.service = hostname

        # If distributed tracing is enabled, propagate the tracing headers to downstream services
        if config.urllib3.distributed_tracing:
            if request_headers is None:
                request_headers = {}
                kwargs["headers"] = request_headers
            HTTPPropagator.inject(span.context, request_headers)

        if config.urllib3.analytics_enabled:
            span.set_tag(ANALYTICS_SAMPLE_RATE_KEY, config.urllib3.get_analytics_sample_rate())

        retries = request_retries.total if isinstance(request_retries, urllib3.util.retry.Retry) else None

        # Call the target function
        response = None
        try:
            response = func(*args, **kwargs)
        finally:
            trace_utils.set_http_meta(
                span,
                integration_config=config.urllib3,
                method=request_method,
                url=request_url,
                status_code=None if response is None else response.status,
                query=parsed_uri.query,
                request_headers=request_headers,
                response_headers={} if response is None else dict(response.headers),
                retries_remain=retries,
            )

        return response
