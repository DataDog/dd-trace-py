"""
Unified Integration Tracing API.

This module provides a unified way for integrations to emit events that are
automatically processed by registered hooks. The key benefits are:

1. Patches emit facts only - no tracer or span logic in patches
2. Hooks contain all intelligence - propagation, tagging, metrics
3. Automatic routing - hooks register for specific events via core.on()
4. Single implementation - shared behavior across integrations of same class

Example usage in a patch::

    from ddtrace.internal.core.integration import (
        IntegrationDescriptor,
        IntegrationEvent,
        SpanConfig,
        dispatch_integration_event,
    )

    # Define integration metadata once
    KAFKA_PRODUCER = IntegrationDescriptor(
        name="kafka",
        category="messaging",
        role="producer",
    )

    def patched_produce(func, instance, args, kwargs):
        topic = kwargs.get("topic", "")

        # Create event with facts only - no tracer logic
        event = IntegrationEvent(
            integration=KAFKA_PRODUCER,
            event_type="send",
            span_config=SpanConfig(
                name="kafka.produce",
                service="kafka",
                span_type="worker",
            ),
            payload={
                "topic": topic,
                "headers": kwargs.get("headers", {}),
                "partition": kwargs.get("partition"),
            },
        )

        # Dispatch handles hook invocation and span lifecycle
        with dispatch_integration_event(event):
            return func(*args, **kwargs)

Event naming convention:
    - integration.event                         # Generic (for subscriber)
    - integration.event.{category}.{event_type} # Specific (hooks register here)
    - integration.event.{category}              # Category-level (hooks register here)

    Examples:
    - integration.event.messaging.send
    - integration.event.messaging.consume
    - integration.event.messaging
    - integration.event.database.query
    - integration.event.http.request
"""

from contextlib import contextmanager
from typing import Generator

from ddtrace.internal import core
from ddtrace.internal.logger import get_logger

from .carrier import CarrierAdapter
from .carrier import DefaultHeaderAdapter
from .descriptor import IntegrationDescriptor
from .event import IntegrationEvent
from .event import SpanConfig
from .hooks import BaseHook
from .hooks import register_hook
from .subscriber import register_subscriber
from .subscriber import unregister_subscriber


log = get_logger(__name__)


# Track which category hooks have been auto-registered
_auto_registered_hooks = set()


def _ensure_hooks_registered(category: str) -> None:
    """
    Lazily register hooks for a category on first use.

    This allows patches to simply use dispatch_integration_event without
    needing to manually call registration functions.
    """
    from .subscriber import register_subscriber

    # Always ensure subscriber is registered
    register_subscriber()

    # Register category-specific hooks if not already done
    if category not in _auto_registered_hooks:
        _auto_registered_hooks.add(category)

        if category == "messaging":
            from .hooks.messaging import register_messaging_hooks

            register_messaging_hooks()


@contextmanager
def dispatch_integration_event(event: IntegrationEvent) -> Generator[IntegrationEvent, None, None]:
    """
    Context manager that dispatches integration.event and manages span lifecycle.

    This is the main entry point for integrations to emit events. It:
    1. Dispatches integration.event (triggers pre-span hooks like context extraction)
    2. Creates a span using the event's SpanConfig
    3. Dispatches integration.event.span_start (triggers span enrichment hooks)
    4. Yields to allow the wrapped function to execute
    5. Dispatches integration.event.span_finish (triggers finish hooks)
    6. Finishes the span

    Usage::

        with dispatch_integration_event(event):
            return original_func(*args, **kwargs)

    Or with span access::

        with dispatch_integration_event(event) as evt:
            result = original_func(*args, **kwargs)
            # evt.span is the created span
            # evt.extracted_context available if propagation hook ran
            return result

    Args:
        event: The IntegrationEvent containing integration metadata and payload

    Yields:
        The same event, with span set after creation
    """
    from ddtrace import tracer

    # Ensure subscriber and hooks are registered for this category
    _ensure_hooks_registered(event.integration.category)

    # Phase 1: Dispatch pre-span event (triggers context extraction hooks)
    core.dispatch("integration.event", (event,))

    # Phase 2: Determine parent context
    parent_ctx = None
    if event.extracted_context is not None:
        # Use context extracted by hooks (e.g., from incoming message headers)
        parent_ctx = event.extracted_context
    elif event.span_config.parent_context_getter is not None:
        # Use custom parent context getter if provided
        parent_ctx = event.span_config.parent_context_getter(event)

    # Phase 3: Create span
    span_config = event.span_config
    span = tracer.start_span(
        name=span_config.name,
        service=span_config.service,
        resource=span_config.resource,
        span_type=span_config.span_type,
        child_of=parent_ctx if parent_ctx is not None else None,
        activate=True,
    )

    # Override start time if provided (for cases where function must execute
    # before span creation, e.g., consumer extracting headers from message)
    if span_config.start_ns is not None:
        span.start_ns = span_config.start_ns

    # Store span in event and context for hooks to access
    event.span = span
    core.set_item("current_span", span)

    # Phase 4: Dispatch span start event (triggers tagging hooks)
    core.dispatch("integration.event.span_start", (event, span))

    try:
        yield event
    except Exception:
        # Set error on span
        import sys

        span.set_exc_info(*sys.exc_info())
        raise
    finally:
        # Check for pre-captured error in payload (for cases where error happened
        # before span creation, e.g., consumer poll raising exception)
        err = event.payload.get("error")
        if err is not None:
            span.set_exc_info(type(err), err, err.__traceback__)

        # Phase 5: Dispatch span finish event
        core.dispatch("integration.event.span_finish", (event, span))

        # Phase 6: Finish span
        span.finish()


__all__ = [
    # Descriptors
    "IntegrationDescriptor",
    "IntegrationEvent",
    "SpanConfig",
    # Carriers
    "CarrierAdapter",
    "DefaultHeaderAdapter",
    # Hooks
    "BaseHook",
    "register_hook",
    # Subscriber
    "register_subscriber",
    "unregister_subscriber",
    # Dispatch
    "dispatch_integration_event",
]
