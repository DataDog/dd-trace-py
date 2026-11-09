import os
import sys
from types import TracebackType
from typing import Optional

from ddtrace import config
from ddtrace._trace._registration import WEB_FRAMEWORK_SUBSCRIBERS
from ddtrace._trace.http_tags import set_instrumentation_resource
from ddtrace._trace.http_tags import set_query_string_tag
from ddtrace._trace.otel.http.resource import record_initial_instrumentation_resource
from ddtrace._trace.otel.http.tags import set_method_tag
from ddtrace._trace.otel.http.tags import set_otel_resource_for_method
from ddtrace._trace.span import Span
from ddtrace._trace.subscribers._base import TracingSubscriber
from ddtrace._trace.subscribers._base import _finish_span
from ddtrace._trace.subscribers._base import _start_span
from ddtrace._trace.trace_handlers import _set_inferred_proxy_tags
from ddtrace.contrib._events.web_framework import WebFrameworkRequestEvent
from ddtrace.contrib._events.web_framework import WebFrameworkRequestExceptionEvent
from ddtrace.contrib.internal import trace_utils
from ddtrace.internal import core
from ddtrace.internal import forksafe
from ddtrace.internal.core import event_hub
from ddtrace.internal.core.subscriber import Subscriber
from ddtrace.internal.logger import get_logger
from ddtrace.internal.otel_semantics import http as otel_http
from ddtrace.internal.settings.standalone import standalone_config
from ddtrace.internal.span_bus import span_from_context


log = get_logger(__name__)


_OWNER_KEY = "web.tracing.owner"
_EVENT_KEY = "web.tracing.event"
_STARTING_KEY = "web.tracing.starting"
_PENDING_END_KEY = "web.tracing.pending_end"


class _WebFrameworkSpanSubscriber(TracingSubscriber):
    """Own request admission and drain only the spans admitted by this subscriber."""

    event_names = (WebFrameworkRequestEvent.event_name,)
    _requested = False
    _standalone_requested = False
    _accepting = False
    _active_requests = 0
    _pid = 0
    _owner_token = object()
    _fork_registered = False
    _start_installed = False
    _end_installed = False

    @classmethod
    def _eligible(cls) -> bool:
        tracer = core.root.get_item("tracer")
        if tracer is None or getattr(tracer, "_shutting_down", False):
            return False
        if standalone_config.apm_opt_out:
            return cls._standalone_requested
        return cls._requested and WEB_FRAMEWORK_SUBSCRIBERS.enabled and tracer.enabled

    @classmethod
    def register(cls) -> None:
        with event_hub._registration_lock:
            cls._requested = True
            cls._initialize()

    @classmethod
    def _initialize(cls) -> None:
        pid = os.getpid()
        if cls._pid != pid:
            cls._owner_token = object()
            cls._pid = pid
        core.on("tracing.enabled", cls._on_tracing_enabled, name=f"{cls.__name__}.enabled")
        core.on("tracing.stopped", cls._on_tracing_stopped, name=f"{cls.__name__}.stopped")
        if not cls._fork_registered:
            forksafe.register(cls._after_fork)
            cls._fork_registered = True
        cls._refresh()

    @classmethod
    def unregister(cls) -> None:
        with event_hub._registration_lock:
            cls._requested = False
            cls._refresh()

    @classmethod
    def _on_tracing_enabled(cls, tracer: object, enabled: bool) -> None:
        if tracer is core.root.get_item("tracer"):
            with event_hub._registration_lock:
                cls._refresh()

    @classmethod
    def _on_tracing_stopped(cls, tracer: object) -> None:
        if tracer is not core.root.get_item("tracer"):
            return
        with event_hub._registration_lock:
            # Shutdown has discarded these traces. Invalidate leases held by
            # callbacks already executing before removing their subscriptions.
            cls._owner_token = object()
            cls._requested = cls._standalone_requested = cls._accepting = False
            cls._active_requests = 0
            core.reset_listeners("context.started.web.request", cls._on_context_started)
            core.reset_listeners("context.ended.web.request", cls._on_context_ended)
            core.reset_listeners("tracing.enabled", cls._on_tracing_enabled)
            core.reset_listeners("tracing.stopped", cls._on_tracing_stopped)
            cls._start_installed = cls._end_installed = False

    @classmethod
    def _after_fork(cls) -> None:
        with event_hub._registration_lock:
            cls._pid = os.getpid()
            cls._owner_token = object()
            cls._active_requests = 0
            cls._refresh()

    @classmethod
    def _refresh(cls) -> None:
        # All callers hold the same fork-safe lock used by bundle registration.
        accepting = cls._eligible()
        cls._accepting = accepting
        if accepting:
            # Completion must be installed before admission opens.
            core.on("context.ended.web.request", cls._on_context_ended, name=f"{cls.__name__}.ended")
            core.on("context.started.web.request", cls._on_context_started, name=f"{cls.__name__}.started")
            cls._start_installed = cls._end_installed = True
        else:
            if cls._start_installed:
                core.reset_listeners("context.started.web.request", cls._on_context_started)
                cls._start_installed = False
            if cls._active_requests:
                core.on("context.ended.web.request", cls._on_context_ended, name=f"{cls.__name__}.ended")
                cls._end_installed = True
            elif cls._end_installed:
                core.reset_listeners("context.ended.web.request", cls._on_context_ended)
                cls._end_installed = False

    @classmethod
    def _on_context_started(cls, ctx: core.ExecutionContext[WebFrameworkRequestEvent]) -> None:
        with event_hub._registration_lock:
            if ctx.get_item(_OWNER_KEY) is not None:
                return
            # Dispatch may already have captured a callback when admission closes.
            if not cls._accepting or not cls._eligible():
                return
            owner = cls._owner_token
            cls._active_requests += 1
            ctx.set_item(_OWNER_KEY, owner)
            ctx.set_item(_EVENT_KEY, ctx.event)
            ctx.event.span_creation_enabled = True
            ctx.set_item(_STARTING_KEY, True)
        event = ctx.event
        previous_span = ctx.get_item("_inner_span")
        span = None
        try:
            span = _start_span(ctx)
            with event_hub._registration_lock:
                ctx.discard_local_item(_STARTING_KEY)
                pending_end = ctx.get_item(_PENDING_END_KEY)
                ctx.discard_local_item(_PENDING_END_KEY)
                discarded = owner is not cls._owner_token
                if pending_end is not None or discarded:
                    ctx.discard_local_item(_OWNER_KEY)
            if discarded:
                cls._release(ctx, owner)
                return
            if pending_end is not None:
                # Ending before __enter__ returns is outside the deferred-event
                # contract, but it must not orphan the span or create a fallback.
                try:
                    span.set_exc_info(*pending_end)
                    span.finish()
                finally:
                    cls._release(ctx, owner)
                return
            for handler in cls._started_handlers:
                handler(ctx)
        except BaseException:
            with event_hub._registration_lock:
                claimed = ctx.get_item(_OWNER_KEY) is owner
                if claimed:
                    ctx.discard_local_item(_OWNER_KEY)
                ctx.discard_local_item(_STARTING_KEY)
                ctx.discard_local_item(_PENDING_END_KEY)
            if claimed:
                try:
                    # _start_span can store its span before a later callback raises.
                    created_span = span or ctx.get_item("_inner_span")
                    if owner is cls._owner_token:
                        if created_span is not None and created_span is not previous_span:
                            created_span.set_exc_info(*sys.exc_info())
                            created_span.finish()
                        else:
                            event.span_creation_enabled = False
                finally:
                    cls._release(ctx, owner)
            raise

    @classmethod
    def _release(cls, ctx: core.ExecutionContext[WebFrameworkRequestEvent], owner: object) -> None:
        with event_hub._registration_lock:
            ctx.discard_local_item(_EVENT_KEY)
            if owner is not cls._owner_token:
                return
            cls._active_requests -= 1
            if not cls._accepting and cls._active_requests == 0:
                core.reset_listeners("context.ended.web.request", cls._on_context_ended)
                cls._end_installed = False

    @classmethod
    def _on_context_ended(
        cls,
        ctx: core.ExecutionContext[WebFrameworkRequestEvent],
        exc_info: tuple[Optional[type], Optional[BaseException], Optional[TracebackType]],
    ) -> None:
        with event_hub._registration_lock:
            owner = cls._owner_token
            if ctx.get_item(_OWNER_KEY) is not owner:
                return
            if ctx.get_item(_STARTING_KEY):
                ctx.set_item(_PENDING_END_KEY, exc_info)
                return
            # Claim once before running callbacks: concurrent completion dispatches
            # must not finish twice or drain another request's completion listener.
            ctx.discard_local_item(_OWNER_KEY)
            # Another producer completion can release ctx.event while this callback
            # is still finishing. Retain the admitted event until our span finishes.
            event = ctx.get_item(_EVENT_KEY)
        try:
            try:
                for handler in cls._ended_handlers:
                    handler(ctx, exc_info)
            finally:
                if owner is cls._owner_token and getattr(event, "_end_span", True):
                    _finish_span(ctx, exc_info, event=event)
        finally:
            cls._release(ctx, owner)

    @classmethod
    def on_started(cls, ctx: core.ExecutionContext) -> None:
        event: WebFrameworkRequestEvent = ctx.get_item(_EVENT_KEY) or ctx.event

        if event.allow_default_resource:
            event.set_resource = True

        if config._otel_trace_semantics_enabled and event.request_method:
            span: Span = span_from_context(ctx)
            set_method_tag(span, event.request_method)
            # Set the currently known resource before sampling; preserve span-start callback replacements.
            record_initial_instrumentation_resource(span, event.resource)
            if event.request_route:
                span._set_attribute(otel_http.ROUTE, event.request_route)
            set_otel_resource_for_method(span, event.request_method, event.request_route)

    @classmethod
    def on_ended(
        cls,
        ctx: core.ExecutionContext,
        exc_info: tuple[Optional[type], Optional[BaseException], Optional[TracebackType]],
    ) -> None:
        event: WebFrameworkRequestEvent = ctx.get_item(_EVENT_KEY) or ctx.event
        status_code = event.response_status_code
        method = event.request_method
        res_headers = event.response_headers

        span: Span = span_from_context(ctx)

        # event.resource can be updated at span finish time
        if event.resource:
            set_instrumentation_resource(span, event.resource)
        elif event.set_resource and status_code is not None:
            set_instrumentation_resource(span, f"{method} {status_code}")

        try:
            trace_utils.set_http_meta(
                span=span,
                integration_config=event.integration_config,
                method=method,
                url=event.request_url,
                # set_http_meta checks only integration_config when deciding
                # whether to trace the query string. aiohttp supports a
                # per-application override instead.
                query=event.query if event.trace_query_string is None else None,
                query_tagged_separately=event.trace_query_string is not None,
                status_code=status_code,
                request_headers=event.request_headers,
                response_headers=dict(res_headers) if res_headers is not None else None,
                headers_are_case_sensitive=event.headers_case_sensitive,
                route=event.request_route,
            )
        except Exception:
            log.debug("%s: error adding response tags", event.integration_config.integration_name, exc_info=True)

        # aiohttp supports per-app trace_query_string overrides that may differ from
        # integration_config.trace_query_string.
        if event.trace_query_string and event.query is not None:
            set_query_string_tag(span, event.query)

        _set_inferred_proxy_tags(span, status_code)
        for tk, tv in event.additional_tags.items():
            span._set_attribute(tk, tv)


class WebFrameworkRequestSubscriber(_WebFrameworkSpanSubscriber):
    """Shared request lifecycle with independent APM and standalone admission."""


class StandaloneWebFrameworkRequestSubscriber:
    """Separate activation handle for standalone products' request span requirements."""

    @classmethod
    def register(cls) -> None:
        with event_hub._registration_lock:
            WebFrameworkRequestSubscriber._standalone_requested = True
            WebFrameworkRequestSubscriber._initialize()

    @classmethod
    def unregister(cls) -> None:
        with event_hub._registration_lock:
            WebFrameworkRequestSubscriber._standalone_requested = False
            WebFrameworkRequestSubscriber._refresh()


class WebFrameworkRequestExceptionSubscriber(Subscriber):
    event_names = (WebFrameworkRequestExceptionEvent.event_name,)

    @classmethod
    def on_event(cls, event: WebFrameworkRequestExceptionEvent) -> None:
        # The context may have released its event at response preparation. Retain
        # the original tracing decision, including when AppSec borrowed a parent span.
        if not event.request_event.span_creation_enabled:
            return
        span = event.request_context.get_item("_inner_span")
        exc_type, exc_value, traceback = event.exc_info
        if span is not None and exc_type is not None and exc_value is not None and traceback is not None:
            span.set_exc_info(exc_type, exc_value, traceback)
