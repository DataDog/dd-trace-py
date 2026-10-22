"""Shared HTTP server tracing scaffold for concrete integration subscribers."""

from typing import Optional

from ddtrace._trace.subscribers._base import TracingEventType
from ddtrace._trace.subscribers._base import TracingSubscriber
from ddtrace.contrib._events.http import HttpRequestDataEvent
from ddtrace.contrib._events.http import HttpRequestPhase
from ddtrace.contrib._events.http import HttpRequestSnapshot
from ddtrace.internal import core


class HttpServerTracingSubscriber(TracingSubscriber[TracingEventType]):
    """Route HTTP snapshots to contexts owned by a concrete subscriber.

    Concrete subscribers must provide request correlation and HTTP attribute handling.
    """

    # No event_names: concrete integration subscribers register themselves.

    @classmethod
    def register(cls) -> None:
        super().register()
        core.on(
            HttpRequestDataEvent.event_name,
            cls._on_http_data,
            name=f"{cls.__name__}.http_data",
        )

    @classmethod
    def unregister(cls) -> None:
        core.reset_listeners(
            HttpRequestDataEvent.event_name,
            cls._on_http_data,
        )
        super().unregister()

    @classmethod
    def _on_http_data(cls, event: HttpRequestDataEvent) -> None:
        snapshot = event.snapshot

        # Resolve only requests owned by this concrete subscriber.
        ctx = cls._find_request_context(snapshot.request_id)
        if ctx is None:
            return

        if snapshot.phase == HttpRequestPhase.ROUTE_RESOLVED:
            cls.on_route_resolved(ctx, snapshot)
        elif snapshot.phase == HttpRequestPhase.COMPLETED:
            cls.on_completed(ctx, snapshot)

    @classmethod
    def _find_request_context(cls, request_id: str) -> Optional[core.ExecutionContext[TracingEventType]]:
        """Return an owned request context, or None for an unrelated request."""
        raise NotImplementedError("Concrete HTTP subscribers must implement request context lookup")

    @classmethod
    def on_route_resolved(cls, ctx: core.ExecutionContext[TracingEventType], snapshot: HttpRequestSnapshot) -> None:
        pass

    @classmethod
    def on_completed(cls, ctx: core.ExecutionContext[TracingEventType], snapshot: HttpRequestSnapshot) -> None:
        # Apply common HTTP status, headers, route, and error attributes.
        ...

    # Lifecycle plumbing, omitted here:
    # - Start the span through TracingSubscriber.
    # - Associate request_id with ctx when tracing is enabled.
    # - Remove that association when the context ends.
    # - Finish the span through TracingSubscriber.
    # - Keep correlation state isolated per concrete subscriber.
