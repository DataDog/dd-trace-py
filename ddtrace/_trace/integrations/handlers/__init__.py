"""
Handler classes for span event processing.

This module provides:
- Handler classes for each event type
- Handler registry for dispatch
- trace_event() context managers for the core API

Handler Hierarchy:
    SpanEventHandler (base)
    ├── DatabaseEventHandler
    ├── HTTPClientEventHandler
    ├── HTTPServerEventHandler
    └── MessagingEventHandler
        └── KafkaEventHandler
"""

from contextlib import asynccontextmanager
from contextlib import contextmanager
from typing import Dict
from typing import Type

from ddtrace._trace.integrations.events import DatabaseEvent
from ddtrace._trace.integrations.events import HTTPClientEvent
from ddtrace._trace.integrations.events import HTTPServerEvent
from ddtrace._trace.integrations.events import KafkaEvent
from ddtrace._trace.integrations.events import MessagingEvent
from ddtrace._trace.integrations.events import SpanEvent
from ddtrace._trace.integrations.handlers.base import DatabaseEventHandler
from ddtrace._trace.integrations.handlers.base import HTTPClientEventHandler
from ddtrace._trace.integrations.handlers.base import HTTPServerEventHandler
from ddtrace._trace.integrations.handlers.base import MessagingEventHandler
from ddtrace._trace.integrations.handlers.base import SpanEventHandler
from ddtrace._trace.integrations.handlers.contrib import KafkaEventHandler


# =============================================================================
# HANDLER REGISTRY
# =============================================================================

# Registry maps event types to handler instances
HANDLER_REGISTRY: Dict[Type[SpanEvent], SpanEventHandler] = {
    SpanEvent: SpanEventHandler(),
    DatabaseEvent: DatabaseEventHandler(),
    HTTPClientEvent: HTTPClientEventHandler(),
    HTTPServerEvent: HTTPServerEventHandler(),
    MessagingEvent: MessagingEventHandler(),
    KafkaEvent: KafkaEventHandler(),
}


def get_handler(event_type: Type[SpanEvent]) -> SpanEventHandler:
    """
    Get the appropriate handler for an event type.

    Walks up the MRO to find the most specific handler.
    """
    for cls in event_type.__mro__:
        if cls in HANDLER_REGISTRY:
            return HANDLER_REGISTRY[cls]
    return HANDLER_REGISTRY[SpanEvent]


def register_handler(event_type: Type[SpanEvent], handler: SpanEventHandler) -> None:
    """Register a custom handler for an event type."""
    HANDLER_REGISTRY[event_type] = handler


# =============================================================================
# CORE API - trace_event()
# =============================================================================


@contextmanager
def trace_event(event: SpanEvent):
    """
    Context manager for tracing an event.

    Dispatches to the appropriate handler based on event type.

    Usage:
        event = DatabaseEvent(
            _span_name="postgres.query",
            _resource=query,
            db_system="postgresql",
            db_statement=query,
        )
        with trace_event(event) as span:
            result = execute_query(query)
            event.db_row_count = len(result)  # Can update event during execution
        # Span is automatically finished here
    """
    handler = get_handler(type(event))
    with handler.trace(event) as span:
        yield span


@asynccontextmanager
async def trace_event_async(event: SpanEvent):
    """
    Async context manager for tracing events.

    Usage:
        async with trace_event_async(event) as span:
            result = await execute_query(query)
            event.db_row_count = len(result)
    """
    handler = get_handler(type(event))
    async with handler.trace_async(event) as span:
        yield span


__all__ = [
    # Handlers
    "SpanEventHandler",
    "DatabaseEventHandler",
    "HTTPClientEventHandler",
    "HTTPServerEventHandler",
    "MessagingEventHandler",
    "KafkaEventHandler",
    # Registry
    "HANDLER_REGISTRY",
    "get_handler",
    "register_handler",
    # Core API
    "trace_event",
    "trace_event_async",
]
