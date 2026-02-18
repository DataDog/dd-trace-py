from types import TracebackType
from typing import Optional
from typing import cast

from ddtrace._trace.subscribers._base import TracingSubscriber
from ddtrace.contrib import trace_utils
from ddtrace.contrib.events.http_client import HttpClientRequestEvent
from ddtrace.contrib.events.httpx import HttpxRequestEvent
from ddtrace.internal import core
from ddtrace.internal.logger import get_logger
from ddtrace.propagation.http import HTTPPropagator


log = get_logger(__name__)


class HttpClientTracingSubscriber(TracingSubscriber):
    """Shared tracing logic for ALL HTTP client integrations.

    httpx, requests, aiohttp, etc. all share this subscriber.
    Adding a feature here applies to every HTTP client integration.
    """

    event_names = (HttpClientRequestEvent.event_name, HttpxRequestEvent.event_name)

    @classmethod
    def on_started(cls, ctx: core.ExecutionContext) -> None:
        event: HttpClientRequestEvent = ctx.event

        if trace_utils.distributed_tracing_enabled(event.config) and event.request_headers is not None:
            HTTPPropagator.inject(ctx.span.context, cast(dict[str, str], event.request_headers))

    @classmethod
    def on_ended(
        cls,
        ctx: core.ExecutionContext,
        exc_info: tuple[Optional[type], Optional[BaseException], Optional[TracebackType]],
    ) -> None:
        event: HttpClientRequestEvent = ctx.event

        try:
            trace_utils.set_http_meta(
                ctx.span,
                event.config,
                method=event.request_method,
                url=event.url,
                target_host=event.target_host,
                status_code=event.response_status_code,
                query=event.query,
                request_headers=event.request_headers,
                response_headers=event.response_headers,
            )
        except Exception:
            log.debug("%s: error adding tags", event.config.integration_name, exc_info=True)
