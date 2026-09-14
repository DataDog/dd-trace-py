from __future__ import annotations

import sys
from typing import Any
from typing import AsyncIterator
from typing import Awaitable
from typing import Callable
from typing import Iterator
from urllib.parse import urlsplit

import niquests
from wrapt import wrap_function_wrapper as _w

from ddtrace import config
from ddtrace._trace.subscribers.http_client import _http_propagation_suppressed
from ddtrace.contrib._events.http_client import HttpClientRequestEvent
from ddtrace.contrib.internal.trace_utils import _sanitized_url
from ddtrace.contrib.internal.trace_utils import ext_service
from ddtrace.internal import core
from ddtrace.internal.schema import schematize_service_name
from ddtrace.internal.settings import env
from ddtrace.internal.utils import get_argument_value
from ddtrace.internal.utils.formats import asbool
from ddtrace.internal.utils.wrappers import unwrap as _u


config._add(  # type: ignore[no-untyped-call]
    "niquests",
    {
        "distributed_tracing": asbool(env.get("DD_NIQUESTS_DISTRIBUTED_TRACING", default=True)),
        "split_by_domain": asbool(env.get("DD_NIQUESTS_SPLIT_BY_DOMAIN", default=False)),
        "default_http_tag_query_string": config._http_client_tag_query_string,
        # The schema function is selected dynamically and has no stable callable type.
        "_default_service": schematize_service_name("niquests"),  # type: ignore[operator]
    },
)


def get_version() -> str:
    return str(getattr(niquests, "__version__", ""))


def _supported_versions() -> dict[str, str]:
    return {"niquests": ">=3.0"}


_CONTEXT_ATTRIBUTE = "_datadog_niquests_context"
_STREAM_ATTRIBUTE = "_datadog_niquests_stream"


def _service_name(netloc: str) -> str | None:
    if config.niquests.split_by_domain:
        return netloc
    return ext_service(None, config.niquests)


def _request_event(request: Any) -> HttpClientRequestEvent:
    request_url = _sanitized_url(str(request.url))
    parsed_url = urlsplit(request_url)
    method = str(request.method or "").upper()
    return HttpClientRequestEvent(
        http_operation="niquests.request",
        service=_service_name(parsed_url.netloc),
        component=config.niquests.integration_name,
        resource=f"{method} {parsed_url.path}",
        integration_config=config.niquests,
        request_method=method,
        request_headers=request.headers,
        request_url=request_url,
        query=parsed_url.query,
        target_host=parsed_url.hostname,
        activate=False,
    )


def _finish_response(response: Any, exc_info: tuple[Any, Any, Any] = (None, None, None)) -> None:
    ctx = getattr(response, _CONTEXT_ATTRIBUTE, None)
    if ctx is None:
        return

    delattr(response, _CONTEXT_ATTRIBUTE)
    ctx.event.set_response(response)
    ctx.dispatch_ended_event(*exc_info)


def _defer_response(ctx: core.ExecutionContext[HttpClientRequestEvent], response: Any, stream: bool) -> None:
    # AIDEV-NOTE: Streamed and multiplexed responses outlive Session.send(). Keep the
    # typed event on the response and finish it from consumption, close, or gather hooks.
    setattr(response, _CONTEXT_ATTRIBUTE, ctx)
    setattr(response, _STREAM_ATTRIBUTE, stream)


def _wrap_send(wrapped: Callable[..., Any], instance: Any, args: tuple[Any, ...], kwargs: dict[str, Any]) -> Any:
    request = get_argument_value(args, kwargs, 0, "request")
    if request is None or getattr(request, "url", None) is None:
        return wrapped(*args, **kwargs)

    event = _request_event(request)
    with core.context_with_event(event, dispatch_end_event=False) as ctx:
        try:
            response = wrapped(*args, **kwargs)
        except BaseException:
            ctx.dispatch_ended_event(*sys.exc_info())
            raise

        stream = bool(kwargs.get("stream", getattr(instance, "stream", False)))
        if stream or bool(getattr(response, "lazy", False)):
            _defer_response(ctx, response, stream)
        else:
            event.set_response(response)
            ctx.dispatch_ended_event()
        return response


async def _wrap_async_send(
    wrapped: Callable[..., Awaitable[Any]], instance: Any, args: tuple[Any, ...], kwargs: dict[str, Any]
) -> Any:
    request = get_argument_value(args, kwargs, 0, "request")
    if request is None or getattr(request, "url", None) is None:
        return await wrapped(*args, **kwargs)

    event = _request_event(request)
    with core.context_with_event(event, dispatch_end_event=False) as ctx:
        try:
            response = await wrapped(*args, **kwargs)
        except BaseException:
            ctx.dispatch_ended_event(*sys.exc_info())
            raise

        stream = bool(kwargs.get("stream", getattr(instance, "stream", False)))
        if stream or bool(getattr(response, "lazy", False)):
            _defer_response(ctx, response, stream)
        else:
            event.set_response(response)
            ctx.dispatch_ended_event()
        return response


def _wrap_adapter_send(
    wrapped: Callable[..., Any], instance: Any, args: tuple[Any, ...], kwargs: dict[str, Any]
) -> Any:
    # AIDEV-NOTE: Suppress only nested transport injection. The urllib3 span remains
    # observable when that integration is patched, but wire headers identify this request span.
    token = _http_propagation_suppressed.set(True)
    try:
        return wrapped(*args, **kwargs)
    finally:
        _http_propagation_suppressed.reset(token)


async def _wrap_async_adapter_send(
    wrapped: Callable[..., Awaitable[Any]], instance: Any, args: tuple[Any, ...], kwargs: dict[str, Any]
) -> Any:
    token = _http_propagation_suppressed.set(True)
    try:
        return await wrapped(*args, **kwargs)
    finally:
        _http_propagation_suppressed.reset(token)


def _wrap_iter_content(
    wrapped: Callable[..., Iterator[Any]], instance: Any, args: tuple[Any, ...], kwargs: dict[str, Any]
) -> Iterator[Any]:
    try:
        iterator = wrapped(*args, **kwargs)
    except BaseException:
        _finish_response(instance, sys.exc_info())
        raise

    def traced_iterator() -> Iterator[Any]:
        try:
            yield from iterator
        except GeneratorExit:
            _finish_response(instance)
            raise
        except BaseException:
            _finish_response(instance, sys.exc_info())
            raise
        else:
            _finish_response(instance)

    return traced_iterator()


async def _wrap_async_iter_content(
    wrapped: Callable[..., Awaitable[AsyncIterator[Any]]],
    instance: Any,
    args: tuple[Any, ...],
    kwargs: dict[str, Any],
) -> AsyncIterator[Any]:
    try:
        iterator = await wrapped(*args, **kwargs)
    except BaseException:
        _finish_response(instance, sys.exc_info())
        raise

    async def traced_iterator() -> AsyncIterator[Any]:
        try:
            async for item in iterator:
                yield item
        except GeneratorExit:
            _finish_response(instance)
            raise
        except BaseException:
            _finish_response(instance, sys.exc_info())
            raise
        else:
            _finish_response(instance)

    return traced_iterator()


def _wrap_close(wrapped: Callable[..., Any], instance: Any, args: tuple[Any, ...], kwargs: dict[str, Any]) -> Any:
    try:
        result = wrapped(*args, **kwargs)
    except BaseException:
        _finish_response(instance, sys.exc_info())
        raise
    _finish_response(instance)
    return result


async def _wrap_async_close(
    wrapped: Callable[..., Awaitable[Any]], instance: Any, args: tuple[Any, ...], kwargs: dict[str, Any]
) -> Any:
    try:
        result = await wrapped(*args, **kwargs)
    except BaseException:
        _finish_response(instance, sys.exc_info())
        raise
    _finish_response(instance)
    return result


def _wrap_future_handler(
    wrapped: Callable[..., Any], instance: Any, args: tuple[Any, ...], kwargs: dict[str, Any]
) -> Any:
    response = get_argument_value(args, kwargs, 0, "response")
    try:
        result = wrapped(*args, **kwargs)
    except BaseException:
        _finish_response(response, sys.exc_info())
        raise

    if result is not None and result is not response and hasattr(response, _CONTEXT_ATTRIBUTE):
        _defer_response(
            getattr(response, _CONTEXT_ATTRIBUTE), result, bool(getattr(response, _STREAM_ATTRIBUTE, False))
        )
        delattr(response, _CONTEXT_ATTRIBUTE)
    elif not bool(getattr(response, _STREAM_ATTRIBUTE, False)) and not bool(getattr(response, "lazy", False)):
        _finish_response(response)
    return result


async def _wrap_async_future_handler(
    wrapped: Callable[..., Awaitable[Any]], instance: Any, args: tuple[Any, ...], kwargs: dict[str, Any]
) -> Any:
    response = get_argument_value(args, kwargs, 0, "response")
    try:
        result = await wrapped(*args, **kwargs)
    except BaseException:
        _finish_response(response, sys.exc_info())
        raise

    if result is not None and result is not response and hasattr(response, _CONTEXT_ATTRIBUTE):
        _defer_response(
            getattr(response, _CONTEXT_ATTRIBUTE), result, bool(getattr(response, _STREAM_ATTRIBUTE, False))
        )
        delattr(response, _CONTEXT_ATTRIBUTE)
    elif not bool(getattr(response, _STREAM_ATTRIBUTE, False)) and not bool(getattr(response, "lazy", False)):
        _finish_response(response)
    return result


def patch() -> None:
    if getattr(niquests, "_datadog_patch", False):
        return

    niquests._datadog_patch = True
    _w(niquests.Session, "send", _wrap_send)
    _w(niquests.adapters.HTTPAdapter, "send", _wrap_adapter_send)
    _w(niquests.models.Response, "iter_content", _wrap_iter_content)
    _w(niquests.models.Response, "close", _wrap_close)

    if hasattr(niquests, "AsyncSession"):
        _w(niquests.AsyncSession, "send", _wrap_async_send)
    if hasattr(niquests.models, "AsyncResponse"):
        _w(niquests.models.AsyncResponse, "iter_content", _wrap_async_iter_content)
        _w(niquests.models.AsyncResponse, "close", _wrap_async_close)
    if hasattr(niquests.adapters.HTTPAdapter, "_future_handler"):
        _w(niquests.adapters.HTTPAdapter, "_future_handler", _wrap_future_handler)
    if hasattr(niquests.adapters, "AsyncHTTPAdapter"):
        _w(niquests.adapters.AsyncHTTPAdapter, "send", _wrap_async_adapter_send)
        if hasattr(niquests.adapters.AsyncHTTPAdapter, "_future_handler"):
            _w(niquests.adapters.AsyncHTTPAdapter, "_future_handler", _wrap_async_future_handler)


def unpatch() -> None:
    if not getattr(niquests, "_datadog_patch", False):
        return

    niquests._datadog_patch = False
    _u(niquests.Session, "send")
    _u(niquests.adapters.HTTPAdapter, "send")
    _u(niquests.models.Response, "iter_content")
    _u(niquests.models.Response, "close")

    if hasattr(niquests, "AsyncSession"):
        _u(niquests.AsyncSession, "send")
    if hasattr(niquests.models, "AsyncResponse"):
        _u(niquests.models.AsyncResponse, "iter_content")
        _u(niquests.models.AsyncResponse, "close")
    if hasattr(niquests.adapters.HTTPAdapter, "_future_handler"):
        _u(niquests.adapters.HTTPAdapter, "_future_handler")
    if hasattr(niquests.adapters, "AsyncHTTPAdapter"):
        _u(niquests.adapters.AsyncHTTPAdapter, "send")
        if hasattr(niquests.adapters.AsyncHTTPAdapter, "_future_handler"):
            _u(niquests.adapters.AsyncHTTPAdapter, "_future_handler")
