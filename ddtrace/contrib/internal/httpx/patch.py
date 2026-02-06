import os
from typing import Any
from typing import Optional

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
HTTP_REQUEST_TAGS = {COMPONENT: config.httpx.integration_name, SPAN_KIND: SpanKind.CLIENT}


def get_version() -> str:
    return getattr(httpx, "__version__", "")


config._add(
    "httpx",
    {
        "distributed_tracing": asbool(os.getenv("DD_HTTPX_DISTRIBUTED_TRACING", default=True)),
        "split_by_domain": asbool(os.getenv("DD_HTTPX_SPLIT_BY_DOMAIN", default=False)),
        "default_http_tag_query_string": config._http_client_tag_query_string,
    },
)


def _supported_versions() -> dict[str, str]:
    return {"httpx": ">=0.25"}


def _get_service_name(request: httpx.Request) -> Optional[str]:
    if config.httpx.split_by_domain:
        if hasattr(request.url, "netloc"):
            return ensure_text(request.url.netloc, errors="backslashreplace")

        service = ensure_binary(request.url.host)
        if request.url.port:
            service += b":" + ensure_binary(str(request.url.port))
        return ensure_text(service, errors="backslashreplace")
    return ext_service(None, config.httpx)


def _wrapped_sync_send_single_request(
    wrapped: BoundFunctionWrapper, instance: httpx.Client, args: tuple[httpx.Request], kwargs: dict[str, Any]
) -> httpx.Response:
    req = get_argument_value(args, kwargs, 0, "request")
    with core.context_with_data(
        "httpx.client._send_single_request",
        request=req,
    ) as ctx:
        resp = None
        try:
            resp = wrapped(*args, **kwargs)
            return resp
        finally:
            ctx.set_item("response", resp)


async def _wrapped_async_send_single_request(
    wrapped: BoundFunctionWrapper, instance: httpx.AsyncClient, args: tuple[httpx.Request], kwargs: dict[str, Any]
):
    req = get_argument_value(args, kwargs, 0, "request")
    with core.context_with_data(
        "httpx.client._send_single_request",
        request=req,
    ) as ctx:
        resp = None
        try:
            resp = await wrapped(*args, **kwargs)
            return resp
        finally:
            ctx.set_item("response", resp)


async def _wrapped_async_send(
    wrapped: BoundFunctionWrapper, instance: httpx.AsyncClient, args: tuple[httpx.Request], kwargs: dict[str, Any]
):
    req = get_argument_value(args, kwargs, 0, "request")

    with core.context_with_data(
        "httpx.request",
        call_trace=True,
        span_name=schematize_url_operation("http.request", protocol="http", direction=SpanDirection.OUTBOUND),
        span_type=SpanTypes.HTTP,
        service=_get_service_name(req),
        tags=HTTP_REQUEST_TAGS,
        request=req,
    ) as ctx:
        resp = None
        try:
            resp = await wrapped(*args, **kwargs)
            return resp
        finally:
            ctx.set_item("response", resp)


def _wrapped_sync_send(
    wrapped: BoundFunctionWrapper, instance: httpx.AsyncClient, args: tuple[httpx.Request], kwargs: dict[str, Any]
):
    req = get_argument_value(args, kwargs, 0, "request")

    with core.context_with_data(
        "httpx.request",
        call_trace=True,
        span_name=schematize_url_operation("http.request", protocol="http", direction=SpanDirection.OUTBOUND),
        span_type=SpanTypes.HTTP,
        service=_get_service_name(req),
        tags=HTTP_REQUEST_TAGS,
        request=req,
    ) as ctx:
        resp = None
        try:
            resp = wrapped(*args, **kwargs)
            return resp
        finally:
            ctx.set_item("response", resp)


def patch() -> None:
    if getattr(httpx, "_datadog_patch", False):
        return

    httpx._datadog_patch = True

    _w(httpx.Client, "send", _wrapped_sync_send)
    _w(httpx.AsyncClient, "send", _wrapped_async_send)
    _w(httpx.Client, "_send_single_request", _wrapped_sync_send_single_request)
    _w(httpx.AsyncClient, "_send_single_request", _wrapped_async_send_single_request)


def unpatch() -> None:
    if not getattr(httpx, "_datadog_patch", False):
        return

    httpx._datadog_patch = False

    _u(httpx.AsyncClient, "send")
    _u(httpx.Client, "send")
    _u(httpx.Client, "_send_single_request")
    _u(httpx.AsyncClient, "_send_single_request")
