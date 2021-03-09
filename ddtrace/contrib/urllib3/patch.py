import urllib3

from ddtrace import config
from ddtrace.pin import Pin
from ddtrace.vendor.wrapt import wrap_function_wrapper as _w

from .. import trace_utils
from ...compat import parse
from ...constants import ANALYTICS_SAMPLE_RATE_KEY
from ...ext import SpanTypes
from ...ext import http
from ...propagation.http import HTTPPropagator
from ...utils.formats import asbool
from ...utils.formats import get_env
from ...utils.http import sanitize_url_for_tag
from ...utils.wrappers import unwrap as _u


# Ports which, if set, will not be used in hostnames/service names
DROP_PORTS = (80, 443)

# Initialize the default config vars
config._add(
    "urllib3",
    {
        "_default_service": "urllib3",
        "distributed_tracing": asbool(get_env("urllib3", "distributed_tracing", default=True)),
        "analytics_enabled": asbool(get_env("urllib3", "analytics_enabled", default=False)),
        "analytics_sample_rate": float(get_env("urllib3", "analytics_sample_rate", default=1.0)),
        "trace_query_string": asbool(get_env("urllib3", "trace_query_string", default=False)),
        "split_by_domain": asbool(get_env("urllib3", "split_by_domain", default=False)),
    },
)


def patch():
    """Enable tracing for all urllib3 requests"""
    if getattr(urllib3, "__datadog_patch", False):
        return
    setattr(urllib3, "__datadog_patch", True)

    _w("urllib3", "connectionpool.HTTPConnectionPool.urlopen", _wrap_urlopen)


def unpatch():
    """Disable trace for all urllib3 requests"""
    if getattr(urllib3, "__datadog_patch", False):
        setattr(urllib3, "__datadog_patch", False)

        _u(urllib3.connectionpool.HTTPConnectionPool, "urlopen")


def _infer_argument_value(args, kwargs, pos, kw, default=None):
    """
    This function parses the value of a target function argument that may have been
    passed in as a positional argument or a keyword argument. Because monkey-patched
    functions do not define the same signature as their target function, the value of
    arguments must be inferred from the packed args and kwargs.

    Keyword arguments are prioritized, followed by the positional argument, followed
    by the default value, if any is set.

    :param args: Positional arguments
    :param kwargs: Keyword arguments
    :param pos: The positional index of the argument if passed in as a positional arg
    :param kw: The name of the keyword if passed in as a keyword argument
    :param default: The default value to return if the argument was not found in args or kwaergs
    :return: The value of the target argument
    """
    if kw in kwargs:
        return kwargs[kw]

    if pos < len(args):
        return args[pos]

    return default


def _wrap_urlopen(func, instance, args, kwargs):
    """
    Wrapper function for the lower-level urlopen in urllib3

    :param func: The original target function "urlopen"
    :param instance: The patched instance of ``HTTPConnectionPool``
    :param args: Positional arguments from the target function
    :param kwargs: Keyword arguments from the target function
    :return: The ``HTTPResponse`` from the target function
    """
    request_method = _infer_argument_value(args, kwargs, 0, "method")
    request_url = _infer_argument_value(args, kwargs, 1, "url")
    request_headers = _infer_argument_value(args, kwargs, 3, "headers")
    request_retries = _infer_argument_value(args, kwargs, 4, "retries")

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
    sanitized_url = sanitize_url_for_tag(request_url)

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
        if isinstance(request_retries, urllib3.util.retry.Retry):
            span.set_tag(http.RETRIES_REMAIN, str(request_retries.total))

        # Call the target function
        response = None
        try:
            response = func(*args, **kwargs)
        finally:
            span.error = response is None or int(response.status >= 500)
            trace_utils.set_http_meta(
                span,
                integration_config=config.urllib3,
                method=request_method,
                url=sanitized_url,
                status_code=None if response is None else response.status,
                query=parsed_uri.query,
                request_headers=request_headers,
                response_headers={} if response is None else dict(response.headers),
            )

        return response
