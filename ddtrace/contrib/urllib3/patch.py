from urllib import parse

import urllib3
import ddtrace
from ddtrace import config
from ddtrace.vendor.wrapt import wrap_function_wrapper as _w
from ddtrace.http import store_request_headers, store_response_headers

from ...ext import http
from ...utils.wrappers import unwrap as _u
from ...utils.formats import asbool, get_env
from ...propagation.http import HTTPPropagator
from ...constants import ANALYTICS_SAMPLE_RATE_KEY


# Initialize the default config vars
config._add(
    "urllib3",
    {
        "service_name": get_env("urllib3", "service_name", "urllib3"),
        "distributed_tracing": asbool(get_env("urllib3", "distributed_tracing", True)),
        "analytics_enabled": asbool(get_env("urllib3", "analytics_enabled", False)),
        "analytics_sample_rate": float(get_env("urllib3", "analytics_sample_rate", 1.0)),
        "trace_query_string": asbool(get_env("urllib3", "trace_query_string", False)),
        "split_by_domain": asbool(get_env("urllib3", "split_by_domain", True)),
    },
)


def patch():
    """Enable tracing for all urllib3 requests"""
    if getattr(urllib3, "__datadog_patch", False):
        return
    setattr(urllib3, "__datadog_patch", True)

    _w("urllib3.connectionpool", "HTTPConnectionPool.urlopen", _wrap_urlopen)


def unpatch():
    """Disable trace for all urllib3 requests"""
    if getattr(urllib3, "__datadog_patch", False):
        setattr(urllib3, "__datadog_patch", False)

        _u(urllib3.connectionpool.HTTPConnectionPool, "urlopen")


# TODO before merge: opportunity to consolidate this fn with "requests" integration
def _extract_service_name(span, hostname, split_by_domain):
    if split_by_domain:
        return hostname

    service_name = config.urllib3["service_name"]
    if span._parent is not None and span._parent.service is not None:
        service_name = span._parent.service
    return service_name


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


def _wrap_urlopen(func, obj, args, kwargs):
    """
    Wrapper function for the lower-level urlopen in urllib3

    :param func: The original target function "urlopen"
    :param obj: The patched instance of ``HTTPConnectionPool``
    :param args: Positional arguments from the target function
    :param kwargs: Keyword arguments from the target function
    :return: The ``HTTPResponse`` from the target function
    """
    request_method = _infer_argument_value(args, kwargs, 0, "method")
    request_url = _infer_argument_value(args, kwargs, 1, "url")
    request_headers = _infer_argument_value(args, kwargs, 3, "headers") or {}

    # HTTPConnectionPool allows relative path requests; convert the request_url to an absolute url
    if request_url.startswith("/"):
        request_url = parse.urlunparse(
            (
                obj.scheme,
                "{}:{}".format(obj.host, obj.port) if obj.port else str(obj.host),
                request_url,
                None,
                None,
                None,
            )
        )

    # TODO before merge: abstract into utils/http.py to share with requests/aiohttp
    parsed_uri = parse.urlparse(request_url)
    hostname = parsed_uri.netloc
    sanitized_url = parse.urlunparse(
        (
            parsed_uri.scheme,
            parsed_uri.netloc,
            parsed_uri.path,
            parsed_uri.params,
            None,  # drop parsed_uri.query
            parsed_uri.fragment,
        )
    )

    tracer = getattr(obj, "datadog_tracer", ddtrace.tracer)

    if not tracer.enabled:
        return func(*args, **kwargs)

    with tracer.trace("urllib3.urlopen", span_type=http.TYPE) as span:

        span.service = _extract_service_name(span, hostname, config.urllib3["split_by_domain"])

        # If distributed tracing is enabled, propagate the tracing headers to downstream services
        if config.urllib3["distributed_tracing"]:
            propagator = HTTPPropagator()
            propagator.inject(span.context, request_headers)

        store_request_headers(request_headers, span, config.urllib3)
        span.set_tag(http.METHOD, request_method)
        span.set_tag(http.URL, sanitized_url)
        if config.urllib3["trace_query_string"]:
            span.set_tag(http.QUERY_STRING, parsed_uri.query)
        if config.urllib3["analytics_enabled"]:
            span.set_tag(ANALYTICS_SAMPLE_RATE_KEY, config.urllib3["analytics_sample_rate"])

        # Call the target function
        resp = func(*args, **kwargs)

        store_response_headers(dict(resp.headers), span, config.urllib3)
        span.set_tag(http.STATUS_CODE, resp.status)
        span.error = int(resp.status >= 500)

        return resp
