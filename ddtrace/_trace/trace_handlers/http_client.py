from types import TracebackType
from typing import Optional
from typing import Tuple

from ddtrace import config
from ddtrace.constants import _SPAN_MEASURED_KEY
from ddtrace.contrib import trace_utils
from ddtrace.contrib.events.http_client import HttpClientEvents
from ddtrace.internal import core
from ddtrace.propagation.http import HTTPPropagator

from .common import _finish_span
from .common import _start_span


def _on_http_send_completed(
    ctx: core.ExecutionContext,
    exc_info: Tuple[Optional[type], Optional[BaseException], Optional[TracebackType]],
) -> None:
    span = ctx.span

    request = ctx.get_item("request")
    response = ctx.get_item("response")
    url = ctx.get_item("url")

    try:
        trace_utils.set_http_meta(
            span,
            config.httpx,
            method=request.method,
            url=url,
            target_host=request.url.host,
            status_code=response.status_code if response else None,
            query=request.url.query,
            request_headers=request.headers,
            response_headers=response.headers if response else None,
        )
    finally:
        _finish_span(ctx, exc_info)


def _on_http_request_start(ctx: core.ExecutionContext, call_trace: bool = True, **kwargs) -> None:
    span = _start_span(ctx, call_trace, **kwargs)
    span._metrics[_SPAN_MEASURED_KEY] = 1

    request = ctx.get_item("request")

    if trace_utils.distributed_tracing_enabled(config.httpx):
        HTTPPropagator.inject(span.context, request.headers)


def listen():
    core.on(f"context.started.{HttpClientEvents.HTTP_REQUEST.value}", _on_http_request_start)
    core.on(f"context.ended.{HttpClientEvents.HTTP_REQUEST.value}", _on_http_send_completed)


listen()
