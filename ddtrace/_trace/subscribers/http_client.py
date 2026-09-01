from contextvars import ContextVar
from types import TracebackType
from typing import Optional
from typing import cast

from ddtrace import config
from ddtrace._trace.otel_http_naming import record_initial_instrumentation_resource
from ddtrace._trace.otel_http_naming import set_otel_http_resource
from ddtrace._trace.subscribers._base import TracingSubscriber
from ddtrace.contrib import trace_utils
from ddtrace.contrib._events.http_client import HttpClientEvents
from ddtrace.contrib._events.http_client import HttpClientRequestEvent
from ddtrace.contrib.internal.trace_utils_base import normalize_http_method
from ddtrace.contrib.internal.trace_utils_base import set_method_tag
from ddtrace.internal import core
from ddtrace.internal.logger import get_logger
from ddtrace.internal.span_bus import span_from_context
from ddtrace.propagation.http import HTTPPropagator


log = get_logger(__name__)

# AIDEV-NOTE: set True by a higher-level integration to skip its own injection
# (e.g. botocore SigV4, requests suppressing the nested urllib3 span). Only
# `patched_api_call`, `_wrapped_api_call`, and `_wrap_adapter_send` may set this,
# and must reset() in try/finally — see PR #18152 for the leak that caused.
_http_propagation_suppressed: ContextVar[bool] = ContextVar("dd_http_propagation_suppressed", default=False)


class HttpClientTracingSubscriber(TracingSubscriber):
    """Shared tracing logic for ALL HTTP client integrations.

    httpx, requests, aiohttp, etc. all share this subscriber.
    Adding a feature here applies to every HTTP client integration.
    """

    event_names = (HttpClientRequestEvent.event_name, HttpClientEvents.HTTPX_REQUEST.value)

    @classmethod
    def on_started(cls, ctx: core.ExecutionContext) -> None:
        event: HttpClientRequestEvent = ctx.event

        if config._otel_trace_semantics_enabled and event.request_method:
            span = span_from_context(ctx)
            set_method_tag(span, event.request_method)
            # Through the shared helper so an unaccepted method reads HTTP here as well as at
            # export; naming it PROPFIND now would show sampling a resource the span never ships.
            # Span-start callbacks have already run. Record ownership only if they left the
            # event-supplied resource untouched, so their custom names remain user-owned.
            record_initial_instrumentation_resource(span, event.resource)
            normalized_method, original_method = normalize_http_method(event.request_method)
            set_otel_http_resource(span, normalized_method, original_method)

        if _http_propagation_suppressed.get():
            return

        if trace_utils.distributed_tracing_enabled(event.integration_config) and event.request_headers is not None:
            HTTPPropagator.inject(span_from_context(ctx).context, cast(dict[str, str], event.request_headers))

    @classmethod
    def on_ended(
        cls,
        ctx: core.ExecutionContext,
        exc_info: tuple[Optional[type], Optional[BaseException], Optional[TracebackType]],
    ) -> None:
        event: HttpClientRequestEvent = ctx.event

        try:
            trace_utils.set_http_meta(
                span_from_context(ctx),
                event.integration_config,
                method=event.request_method,
                url=event.request_url,
                target_host=event.target_host,
                status_code=event.response_status_code,
                status_msg=event.response_status_msg,
                query=event.query,
                request_headers=event.request_headers,
                response_headers=event.response_headers,
                server_address=event.server_address,
                retries_remain=event.retries_remain,
            )
        except Exception:
            log.debug("%s: error adding tags", event.integration_config.integration_name, exc_info=True)
