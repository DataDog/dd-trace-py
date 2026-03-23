import importlib
import io
import json
import os
import sys
from urllib import parse

import urllib3
from wrapt import wrap_function_wrapper as _w

from ddtrace import config
from ddtrace._trace.pin import Pin
from ddtrace.constants import SPAN_KIND
from ddtrace.contrib import trace_utils
from ddtrace.contrib._events.http_client import HttpClientEvents
from ddtrace.contrib._events.http_client import HttpClientRequestEvent
from ddtrace.contrib._events.http_client import HttpClientSendEvent
from ddtrace.contrib.internal.trace_utils import maybe_set_service_source_tag
from ddtrace.ext import SpanKind
from ddtrace.ext import SpanTypes
from ddtrace.ext import net
from ddtrace.internal import core
from ddtrace.internal.constants import COMPONENT
from ddtrace.internal.schema import schematize_service_name
from ddtrace.internal.schema import schematize_url_operation
from ddtrace.internal.schema.span_attribute_schema import SpanDirection
from ddtrace.internal.utils import get_argument_value
from ddtrace.internal.utils.formats import asbool
from ddtrace.internal.utils.wrappers import unwrap as _u
from ddtrace.propagation.http import HTTPPropagator
from ddtrace.trace import tracer


# Ports which, if set, will not be used in hostnames/service names
DROP_PORTS = (80, 443)

# Initialize the default config vars
config._add(
    "urllib3",
    {
        "_default_service": schematize_service_name("urllib3"),
        "distributed_tracing": asbool(os.getenv("DD_URLLIB3_DISTRIBUTED_TRACING", default=True)),
        "default_http_tag_query_string": config._http_client_tag_query_string,
        "split_by_domain": asbool(os.getenv("DD_URLLIB3_SPLIT_BY_DOMAIN", default=False)),
    },
)


def get_version() -> str:
    return getattr(urllib3, "__version__", "")


def _supported_versions() -> dict[str, str]:
    return {"urllib3": ">=1.25.0"}


def patch():
    """Enable tracing for all urllib3 requests"""
    if getattr(urllib3, "__datadog_patch", False):
        return
    urllib3.__datadog_patch = True

    _w("urllib3", "connectionpool.HTTPConnectionPool.urlopen", _wrap_urlopen)
    _w(_get_request_methods_class(), "request", _wrap_request_method)
    if not trace_utils.iswrapped(urllib3.connectionpool.HTTPConnectionPool, "_make_request"):
        _w("urllib3.connectionpool", "HTTPConnectionPool._make_request", _wrap_make_request)
    Pin().onto(urllib3.connectionpool.HTTPConnectionPool)


def unpatch():
    """Disable trace for all urllib3 requests"""
    if getattr(urllib3, "__datadog_patch", False):
        urllib3.__datadog_patch = False

        _u(urllib3.connectionpool.HTTPConnectionPool, "urlopen")
        _u(_get_request_methods_class(), "request")

    requests_module = sys.modules.get("requests")
    if requests_module is None or not getattr(requests_module, "__datadog_patch", False):
        try:
            _u(urllib3.connectionpool.HTTPConnectionPool, "_make_request")
        except AttributeError:
            pass


def _get_request_methods_class():
    if hasattr(urllib3, "_request_methods"):
        return importlib.import_module("urllib3._request_methods").RequestMethods
    return importlib.import_module("urllib3.request").RequestMethods


def _absolute_request_url(instance, request_url: str) -> str:
    if request_url.startswith("/"):
        return parse.urlunparse(
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
    return request_url


def _set_urllib3_response(event, response) -> None:
    adapted_response = _Urllib3ResponseAdapter(response)
    event.response_status_code = adapted_response.status_code
    event.response_headers = adapted_response.headers
    if isinstance(event, HttpClientRequestEvent):
        event.response = adapted_response


class _Urllib3ResponseAdapter(object):
    def __init__(self, response) -> None:
        self._response = response
        self.status_code = response.status
        self.headers = response.headers

    def json(self):
        if self._response.length and self._response.headers.get("content-type", None) == "application/json":
            length = self._response.length
            body = self._response.read()
            self._response.fp = io.BytesIO(body)
            self._response.length = length
            return json.loads(body)
        raise ValueError("response body is not valid JSON")


def _wrap_request_method(func, instance, args, kwargs):
    request_method = get_argument_value(args, kwargs, 0, "method", optional=True) or ""
    request_url = get_argument_value(args, kwargs, 1, "url", optional=True) or ""
    request_headers = get_argument_value(args, kwargs, 4, "headers", optional=True) or {}

    parsed_uri = parse.urlparse(request_url)
    hostname = parsed_uri.hostname

    with core.context_with_event(
        HttpClientRequestEvent(
            http_operation="urllib3.request",
            service=trace_utils.ext_service(None, config.urllib3),
            component=config.urllib3.integration_name,
            config=config.urllib3,
            request_method=request_method,
            request_headers=request_headers,
            url=request_url,
            query=parsed_uri.query,
            target_host=hostname,
        ),
        context_name_override=HttpClientEvents.URLLIB3_REQUEST.value,
    ) as ctx:
        response = None
        try:
            response = func(*args, **kwargs)
            return response
        finally:
            if response is not None:
                _set_urllib3_response(ctx.event, response)


def _wrap_make_request(func, instance, args, kwargs):
    request_method = get_argument_value(args, kwargs, 1, "method", optional=True) or ""
    request_url = _absolute_request_url(instance, get_argument_value(args, kwargs, 2, "url", optional=True) or "")
    request_headers = get_argument_value(args, kwargs, 4, "headers", optional=True) or {}
    body = get_argument_value(args, kwargs, 3, "body", optional=True)

    with core.context_with_event(
        HttpClientSendEvent(
            url=request_url,
            request_method=request_method,
            request_headers=request_headers,
            request_body=lambda: body,
        ),
        context_name_override=HttpClientEvents.URLLIB3_SEND_REQUEST.value,
    ) as ctx:
        response = None
        try:
            response = func(*args, **kwargs)
            return response
        finally:
            if response is not None:
                _set_urllib3_response(ctx.event, response)


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
    request_headers = get_argument_value(args, kwargs, 3, "headers", optional=True)
    request_retries = get_argument_value(args, kwargs, 4, "retries", optional=True)

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

    with tracer.trace(
        schematize_url_operation("urllib3.request", protocol="http", direction=SpanDirection.OUTBOUND),
        service=trace_utils.ext_service(pin, config.urllib3),
        span_type=SpanTypes.HTTP,
    ) as span:
        maybe_set_service_source_tag(span, config.urllib3)
        span._set_attribute(COMPONENT, config.urllib3.integration_name)

        # set span.kind to the type of operation being performed
        span._set_attribute(SPAN_KIND, SpanKind.CLIENT)

        if config.urllib3.split_by_domain:
            span.service = hostname

        # If distributed tracing is enabled, propagate the tracing headers to downstream services
        if config.urllib3.distributed_tracing:
            if request_headers is None:
                request_headers = {}
                kwargs["headers"] = request_headers
            HTTPPropagator.inject(span.context, request_headers)

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
                target_host=instance.host,
                status_code=None if response is None else response.status,
                query=parsed_uri.query,
                request_headers=request_headers,
                response_headers={} if response is None else dict(response.headers),
                retries_remain=retries,
            )
            span._set_attribute(net.SERVER_ADDRESS, instance.host)

        return response
