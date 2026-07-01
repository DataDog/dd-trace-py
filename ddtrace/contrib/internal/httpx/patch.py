from typing import Any
from typing import Awaitable
from typing import Optional

import httpx
from wrapt import BoundFunctionWrapper
from wrapt import wrap_function_wrapper as _w

from ddtrace import config
from ddtrace.constants import SPAN_KIND
from ddtrace.contrib._events.http_client import HttpClientEvents
from ddtrace.contrib._events.http_client import HttpClientRequestEvent
from ddtrace.contrib._events.http_client import HttpClientSendEvent
from ddtrace.contrib.internal.trace_utils import ext_service
from ddtrace.ext import SpanKind
from ddtrace.internal import core
from ddtrace.internal.compat import ensure_binary
from ddtrace.internal.compat import ensure_text
from ddtrace.internal.constants import COMPONENT
from ddtrace.internal.settings import env
from ddtrace.internal.utils import get_argument_value
from ddtrace.internal.utils.formats import asbool
from ddtrace.internal.utils.version import parse_version
from ddtrace.internal.utils.wrappers import unwrap as _u

from .utils import httpx_url_to_str


HTTPX_VERSION = parse_version(httpx.__version__)
HTTP_REQUEST_TAGS = {COMPONENT: config.httpx.integration_name, SPAN_KIND: SpanKind.CLIENT}


def get_version() -> str:
    return getattr(httpx, "__version__", "")


config._add(
    "httpx",
    {
        "distributed_tracing": asbool(env.get("DD_HTTPX_DISTRIBUTED_TRACING", default=True)),
        "split_by_domain": asbool(env.get("DD_HTTPX_SPLIT_BY_DOMAIN", default=False)),
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
    wrapped: "BoundFunctionWrapper[..., httpx.Response]",
    instance: httpx.Client,
    args: tuple[httpx.Request],
    kwargs: dict[str, Any],
) -> Optional[httpx.Response]:
    req: httpx.Request = get_argument_value(args, kwargs, 0, "request")
    with core.context_with_event(
        event=HttpClientSendEvent(
            request_url=httpx_url_to_str(req.url),
            request_method=req.method,
            request_headers=req.headers,
            request_body=lambda: req.content,
        ),
        context_name_override=HttpClientEvents.HTTPX_SEND_REQUEST.value,
    ) as ctx:
        resp = None
        try:
            resp = wrapped(*args, **kwargs)
            return resp
        finally:
            if resp is not None:
                ctx.event.set_response(resp)


async def _wrapped_async_send_single_request(
    wrapped: "BoundFunctionWrapper[..., Awaitable[httpx.Response]]",
    instance: httpx.AsyncClient,
    args: tuple[httpx.Request],
    kwargs: dict[str, Any],
) -> Optional[httpx.Response]:
    req: httpx.Request = get_argument_value(args, kwargs, 0, "request")
    with core.context_with_event(
        event=HttpClientSendEvent(
            request_url=httpx_url_to_str(req.url),
            request_method=req.method,
            request_headers=req.headers,
            request_body=lambda: req.content,
        ),
        context_name_override=HttpClientEvents.HTTPX_SEND_REQUEST.value,
    ) as ctx:
        resp = None
        try:
            resp = await wrapped(*args, **kwargs)
            return resp
        finally:
            if resp is not None:
                ctx.event.set_response(resp)


async def _wrapped_async_send(
    wrapped: "BoundFunctionWrapper[..., Awaitable[httpx.Response]]",
    instance: httpx.AsyncClient,
    args: tuple[httpx.Request],
    kwargs: dict[str, Any],
) -> Optional[httpx.Response]:
    req: httpx.Request = get_argument_value(args, kwargs, 0, "request")  # type: ignore

    with core.context_with_event(
        HttpClientRequestEvent(
            http_operation="http.request",
            service=_get_service_name(req),
            component=config.httpx.integration_name,
            request_method=req.method,
            request_headers=req.headers,
            integration_config=config.httpx,
            request_url=httpx_url_to_str(req.url),
            query=ensure_text(req.url.query),
            target_host=req.url.host,
        ),
        context_name_override=HttpClientEvents.HTTPX_REQUEST.value,
    ) as ctx:
        resp = None
        try:
            resp = await wrapped(*args, **kwargs)
            return resp
        finally:
            if resp is not None:
                ctx.event.set_response(resp)


def _wrapped_sync_send(
    wrapped: "BoundFunctionWrapper[..., httpx.Response]",
    instance: httpx.AsyncClient,
    args: tuple[httpx.Request],
    kwargs: dict[str, Any],
) -> Optional[httpx.Response]:
    req: httpx.Request = get_argument_value(args, kwargs, 0, "request")  # type: ignore

    with core.context_with_event(
        HttpClientRequestEvent(
            component=config.httpx.integration_name,
            http_operation="http.request",
            service=_get_service_name(req),
            request_method=req.method,
            request_headers=req.headers,
            integration_config=config.httpx,
            request_url=httpx_url_to_str(req.url),
            query=ensure_text(req.url.query),
            target_host=req.url.host,
        ),
        context_name_override=HttpClientEvents.HTTPX_REQUEST.value,
    ) as ctx:
        resp = None
        try:
            resp = wrapped(*args, **kwargs)
            return resp
        finally:
            if resp is not None:
                ctx.event.set_response(resp)


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
