import os
from typing import Any
from typing import Dict
from typing import Tuple

import httpx
from wrapt import BoundFunctionWrapper
from wrapt import wrap_function_wrapper as _w

from ddtrace import config
from ddtrace.constants import SPAN_KIND
from ddtrace.contrib.internal.trace_utils import ext_service
from ddtrace.ext import SpanKind
from ddtrace.ext import SpanTypes
from ddtrace.internal import core
from ddtrace.internal.compat import ensure_binary
from ddtrace.internal.compat import ensure_text
from ddtrace.internal.constants import COMPONENT
from ddtrace.internal.schema import schematize_url_operation
from ddtrace.internal.schema.span_attribute_schema import SpanDirection
from ddtrace.internal.utils import get_argument_value
from ddtrace.internal.utils.formats import asbool
from ddtrace.internal.utils.version import parse_version
from ddtrace.internal.utils.wrappers import unwrap as _u


HTTPX_VERSION = parse_version(httpx.__version__)


def get_version():
    # type: () -> str
    return getattr(httpx, "__version__", "")


config._add(
    "httpx",
    {
        "distributed_tracing": asbool(os.getenv("DD_HTTPX_DISTRIBUTED_TRACING", default=True)),
        "split_by_domain": asbool(os.getenv("DD_HTTPX_SPLIT_BY_DOMAIN", default=False)),
        "default_http_tag_query_string": config._http_client_tag_query_string,
    },
)


def _supported_versions() -> Dict[str, str]:
    return {"httpx": ">=0.25"}


def _url_to_str(url):
    # type: (httpx.URL) -> str
    """
    Helper to convert the httpx.URL parts from bytes to a str
    """
    scheme = url.raw_scheme
    host = url.raw_host
    port = url.port
    raw_path = url.raw_path
    url = scheme + b"://" + host
    if port is not None:
        url += b":" + ensure_binary(str(port))
    url += raw_path

    return ensure_text(url)


def _get_service_name(request):
    if config.httpx.split_by_domain:
        if hasattr(request.url, "netloc"):
            return ensure_text(request.url.netloc, errors="backslashreplace")

        service = ensure_binary(request.url.host)
        if request.url.port:
            service += b":" + ensure_binary(str(request.url.port))
        return ensure_text(service, errors="backslashreplace")
    return ext_service(None, config.httpx)


def http_request_tags():
    return {COMPONENT: config.httpx.integration_name, SPAN_KIND: SpanKind.CLIENT}


async def _wrapped_async_send(
    wrapped: BoundFunctionWrapper,
    instance,  # type: httpx.AsyncClient
    args,  # type: typing.Tuple[httpx.Request]
    kwargs,  # type: typing.Dict[typing.Str, typing.Any]
):
    req = get_argument_value(args, kwargs, 0, "request")

    with core.context_with_data(
        "httpx.request",
        call_trace=True,
        span_name=schematize_url_operation("http.request", protocol="http", direction=SpanDirection.OUTBOUND),
        span_type=SpanTypes.HTTP,
        service=_get_service_name(req),
        tags=http_request_tags(),
    ) as ctx:
        core.dispatch("httpx.send", (ctx, req))

        resp = None
        try:
            resp = await wrapped(*args, **kwargs)
            return resp
        finally:
            core.dispatch("httpx.send.completed", (ctx, req, resp, _url_to_str(req.url)))


def _wrapped_sync_send(
    wrapped: BoundFunctionWrapper, instance: httpx.AsyncClient, args: Tuple[httpx.Request], kwargs: Dict[str, Any]
):
    req = get_argument_value(args, kwargs, 0, "request")

    with core.context_with_data(
        "httpx.request",
        call_trace=True,
        span_name=schematize_url_operation("http.request", protocol="http", direction=SpanDirection.OUTBOUND),
        span_type=SpanTypes.HTTP,
        service=_get_service_name(req),
        tags=http_request_tags(),
    ) as ctx:
        core.dispatch("httpx.send", (ctx, req))

        resp = None
        try:
            resp = wrapped(*args, **kwargs)
            return resp
        finally:
            core.dispatch("httpx.send.completed", (ctx, req, resp, _url_to_str(req.url)))


def patch():
    # type: () -> None
    if getattr(httpx, "_datadog_patch", False):
        return

    httpx._datadog_patch = True

    _w(httpx.Client, "send", _wrapped_sync_send)
    _w(httpx.AsyncClient, "send", _wrapped_async_send)


def unpatch():
    # type: () -> None
    if not getattr(httpx, "_datadog_patch", False):
        return

    httpx._datadog_patch = False

    _u(httpx.AsyncClient, "send")
    _u(httpx.Client, "send")
