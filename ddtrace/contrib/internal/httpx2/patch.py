from typing import Any
from typing import Awaitable
from typing import Optional

import httpx2
from wrapt import BoundFunctionWrapper
from wrapt import wrap_function_wrapper as _w

from ddtrace import config
from ddtrace.contrib._events.http_client import HttpClientEvents
from ddtrace.contrib._events.http_client import HttpClientRequestEvent
from ddtrace.contrib._events.http_client import HttpClientSendEvent
from ddtrace.contrib.internal.httpx.utils import httpx_get_service_name
from ddtrace.contrib.internal.httpx.utils import httpx_url_to_str
from ddtrace.internal import core
from ddtrace.internal.compat import ensure_text
from ddtrace.internal.settings import env
from ddtrace.internal.utils import get_argument_value
from ddtrace.internal.utils.formats import asbool
from ddtrace.internal.utils.wrappers import unwrap as _u


def get_version() -> str:
    return getattr(httpx2, "__version__", "")


config._add(  # type: ignore[no-untyped-call]
    "httpx2",
    {
        "distributed_tracing": asbool(env.get("DD_HTTPX2_DISTRIBUTED_TRACING", default=True)),
        "split_by_domain": asbool(env.get("DD_HTTPX2_SPLIT_BY_DOMAIN", default=False)),
        "default_http_tag_query_string": config._http_client_tag_query_string,
    },
)


def _supported_versions() -> dict[str, str]:
    return {"httpx2": ">=2.0"}


def _wrapped_sync_send_single_request(
    wrapped: "BoundFunctionWrapper[..., httpx2.Response]",
    instance: httpx2.Client,
    args: tuple[httpx2.Request],
    kwargs: dict[str, Any],
) -> Optional[httpx2.Response]:
    request: httpx2.Request = get_argument_value(args, kwargs, 0, "request")

    with core.context_with_event(
        event=HttpClientSendEvent(
            request_url=httpx_url_to_str(request.url),
            request_method=request.method,
            request_headers=request.headers,
            request_body=lambda: request.content,
        ),
        context_name_override=HttpClientEvents.HTTPX_SEND_REQUEST.value,
    ) as ctx:
        response = None
        try:
            response = wrapped(*args, **kwargs)
            return response
        finally:
            if response is not None:
                ctx.event.set_response(response)
    return None


async def _wrapped_async_send_single_request(
    wrapped: "BoundFunctionWrapper[..., Awaitable[httpx2.Response]]",
    instance: httpx2.AsyncClient,
    args: tuple[httpx2.Request],
    kwargs: dict[str, Any],
) -> Optional[httpx2.Response]:
    request: httpx2.Request = get_argument_value(args, kwargs, 0, "request")

    with core.context_with_event(
        event=HttpClientSendEvent(
            request_url=httpx_url_to_str(request.url),
            request_method=request.method,
            request_headers=request.headers,
            request_body=lambda: request.content,
        ),
        context_name_override=HttpClientEvents.HTTPX_SEND_REQUEST.value,
    ) as ctx:
        response = None
        try:
            response = await wrapped(*args, **kwargs)
            return response
        finally:
            if response is not None:
                ctx.event.set_response(response)
    return None


async def _wrapped_async_send(
    wrapped: "BoundFunctionWrapper[..., Awaitable[httpx2.Response]]",
    instance: httpx2.AsyncClient,
    args: tuple[httpx2.Request],
    kwargs: dict[str, Any],
) -> Optional[httpx2.Response]:
    request: httpx2.Request = get_argument_value(args, kwargs, 0, "request")

    with core.context_with_event(
        HttpClientRequestEvent(
            http_operation="http.request",
            service=httpx_get_service_name(request, config.httpx2),
            component=config.httpx2.integration_name,
            request_method=request.method,
            request_headers=request.headers,
            integration_config=config.httpx2,
            request_url=httpx_url_to_str(request.url),
            query=ensure_text(request.url.query),
            target_host=request.url.host,
        ),
        context_name_override=HttpClientEvents.HTTPX_REQUEST.value,
    ) as ctx:
        response = None
        try:
            response = await wrapped(*args, **kwargs)
            return response
        finally:
            if response is not None:
                ctx.event.set_response(response)
    return None


def _wrapped_sync_send(
    wrapped: "BoundFunctionWrapper[..., httpx2.Response]",
    instance: httpx2.Client,
    args: tuple[httpx2.Request],
    kwargs: dict[str, Any],
) -> Optional[httpx2.Response]:
    request: httpx2.Request = get_argument_value(args, kwargs, 0, "request")

    with core.context_with_event(
        HttpClientRequestEvent(
            component=config.httpx2.integration_name,
            http_operation="http.request",
            service=httpx_get_service_name(request, config.httpx2),
            request_method=request.method,
            request_headers=request.headers,
            integration_config=config.httpx2,
            request_url=httpx_url_to_str(request.url),
            query=ensure_text(request.url.query),
            target_host=request.url.host,
        ),
        context_name_override=HttpClientEvents.HTTPX_REQUEST.value,
    ) as ctx:
        response = None
        try:
            response = wrapped(*args, **kwargs)
            return response
        finally:
            if response is not None:
                ctx.event.set_response(response)
    return None


def patch() -> None:
    if getattr(httpx2, "_datadog_patch", False):
        return

    httpx2._datadog_patch = True

    _w(httpx2.Client, "send", _wrapped_sync_send)
    _w(httpx2.AsyncClient, "send", _wrapped_async_send)
    _w(httpx2.Client, "_send_single_request", _wrapped_sync_send_single_request)
    _w(httpx2.AsyncClient, "_send_single_request", _wrapped_async_send_single_request)


def unpatch() -> None:
    if not getattr(httpx2, "_datadog_patch", False):
        return

    httpx2._datadog_patch = False

    _u(httpx2.AsyncClient, "send")
    _u(httpx2.Client, "send")
    _u(httpx2.Client, "_send_single_request")
    _u(httpx2.AsyncClient, "_send_single_request")
