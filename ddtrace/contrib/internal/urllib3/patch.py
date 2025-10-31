import os
from typing import Dict
from urllib import parse

import urllib3
from wrapt import wrap_function_wrapper as _w

from ddtrace import config
from ddtrace._trace import trace_handlers as _trace_handlers
from ddtrace._trace.pin import Pin
from ddtrace.constants import SPAN_KIND
from ddtrace.contrib import trace_utils
from ddtrace.ext import SpanKind
from ddtrace.ext import SpanTypes
from ddtrace.ext import net
from ddtrace.internal import core
from ddtrace.internal.constants import COMPONENT
from ddtrace.internal.schema import schematize_service_name
from ddtrace.internal.schema import schematize_url_operation
from ddtrace.internal.schema.span_attribute_schema import SpanDirection
from ddtrace.internal.utils import ArgumentError
from ddtrace.internal.utils import get_argument_value
from ddtrace.internal.utils import set_argument_value
from ddtrace.internal.utils.formats import asbool
from ddtrace.internal.utils.wrappers import unwrap as _u
from ddtrace.propagation.http import HTTPPropagator


# Ports which, if set, will not be used in hostnames/service names
DROP_PORTS = (80, 443)

# Initialize the default config vars
config._add(
    "urllib3",
    {
        "tracing_enabled": asbool(os.getenv("DD_TRACE_URLLIB3_ENABLED", True)),
        "_default_service": schematize_service_name("urllib3"),
        "distributed_tracing": asbool(os.getenv("DD_URLLIB3_DISTRIBUTED_TRACING", default=True)),
        "default_http_tag_query_string": config._http_client_tag_query_string,
        "split_by_domain": asbool(os.getenv("DD_URLLIB3_SPLIT_BY_DOMAIN", default=False)),
    },
)


def get_version():
    # type: () -> str
    return getattr(urllib3, "__version__", "")


def _supported_versions() -> Dict[str, str]:
    return {"urllib3": ">=1.25.0"}


def patch():
    """Enable patching for all urllib3 requests"""
    if getattr(urllib3, "__datadog_patch", False):
        return
    urllib3.__datadog_patch = True

    if config.urllib3["tracing_enabled"]:
        core.on("context.started.urllib3.urlopen", _urllib3_open_start_span)
        core.on("context.ended.urllib3.urlopen", _urllib3_open_finish_span)

    _w("urllib3", "connectionpool.HTTPConnectionPool.urlopen", _wrap_urlopen(urllib3))
    _w("urllib3", "connectionpool.HTTPConnectionPool._make_request", _wrap_make_request)

    Pin().onto(urllib3.connectionpool.HTTPConnectionPool)


def unpatch():
    """Disable patching for all urllib3 requests"""
    if getattr(urllib3, "__datadog_patch", False):
        urllib3.__datadog_patch = False

        _u(urllib3.connectionpool.HTTPConnectionPool, "urlopen")
        _u(urllib3.connectionpool.HTTPConnectionPool, "_make_request")
        core.reset_listeners("context.started.urllib3.urlopen")
        core.reset_listeners("context.ended.urllib3.urlopen")


def _wrap_make_request(func, instance, args, kwargs) -> urllib3.HTTPResponse:
    """
    Wrapper function for the lower-level _make_request in urllib3

    _make_request is called by urlopen and contains the effective request headers
    after the configuration of proxies by urllib3

    :param func: The original target function "urlopen"
    :param instance: The patched instance of ``HTTPConnectionPool``
    :param args: Positional arguments from the target function
    :param kwargs: Keyword arguments from the target function
    :return: The ``HTTPResponse`` from the target function
    """

    request_headers = get_argument_value(args, kwargs, 4, "headers")
    core.dispatch("urllib3._make_request.collect_headers", (request_headers,))
    return func(*args, **kwargs)


@trace_utils.with_traced_module
def _wrap_urlopen(_urllib3_mod, pin, func, instance, args, kwargs):
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
    request_body = get_argument_value(args, kwargs, 2, "body")

    try:
        request_headers = get_argument_value(args, kwargs, 3, "headers")
    except ArgumentError:
        request_headers = None

    try:
        request_retries = get_argument_value(args, kwargs, 4, "retries")
    except ArgumentError:
        request_retries = None

    retries = request_retries.total if isinstance(request_retries, urllib3.util.retry.Retry) else None

    if request_url.startswith("/") and instance is not None:
        host_port = (
            f"{instance.host}:{instance.port}"
            if getattr(instance, "port", None) and instance.port not in DROP_PORTS
            else str(instance.host)
        )
        request_url = parse.urlunparse((instance.scheme, host_port, request_url, None, None, None))

    parsed_uri = parse.urlparse(request_url)

    with core.context_with_data(
        "urllib3.urlopen",
        span_name=schematize_url_operation("urllib3.request", protocol="http", direction=SpanDirection.OUTBOUND),
        pin=pin,
        service=trace_utils.ext_service(pin, config.urllib3),
        span_type=SpanTypes.HTTP,
        integration_config=config.urllib3,
        tags={COMPONENT: config.urllib3.integration_name, SPAN_KIND: SpanKind.CLIENT},
        request_url=request_url,
        request_method=request_method,
        request_headers=request_headers,
        request_body=request_body,
        retries=retries,
        parsed_uri=parsed_uri,
        host=instance.host,
        downstream_request_full_url=request_url,
    ) as ctx:
        response = None
        try:
            response = func(*args, **kwargs)
            return response
        finally:
            ctx.set_item("response", response)


def _urllib3_open_start_span(ctx: core.ExecutionContext):
    span = _trace_handlers._start_span(ctx)
    if not span:
        return

    parsed_uri = ctx.get_item("parsed_uri")
    if span is not None and config.urllib3.split_by_domain and parsed_uri:
        span.service = parsed_uri.netloc

    if config.urllib3.distributed_tracing:
        request_headers = ctx.get_item("request_headers")
        if request_headers is None:
            request_headers = {}
            ctx.set_item("request_headers", request_headers)
        HTTPPropagator.inject(span.context, request_headers)


def _urllib3_open_finish_span(ctx: core.ExecutionContext, exc_info):
    span = ctx.span
    if not span:
        return

    request_method, request_url, host, parsed_uri, request_headers, retries, response = ctx.get_items(
        ["request_method", "request_url", "host", "parsed_uri", "request_headers", "retries", "response"]
    )

    span._set_tag_str(net.SERVER_ADDRESS, host)

    status_code = None if response is None else response.status
    response_headers = {} if response is None else dict(response.headers)

    trace_utils.set_http_meta(
        span,
        integration_config=config.urllib3,
        method=request_method,
        url=request_url,
        target_host=host,
        status_code=status_code,
        query=parsed_uri.query,
        request_headers=request_headers,
        response_headers=response_headers,
        retries_remain=retries,
    )

    _trace_handlers._finish_span(ctx, exc_info)
