"""
Generic integration event subscriber.

This subscriber receives all integration.event dispatches and automatically
routes them to specific event names based on the event data. Hooks register
with core.on() for those specific event names.
"""

from typing import TYPE_CHECKING

from ddtrace.internal import core
from ddtrace.internal.logger import get_logger


if TYPE_CHECKING:
    from .event import IntegrationEvent


log = get_logger(__name__)

_subscriber_registered = False


def _on_integration_event(event: "IntegrationEvent") -> None:
    """
    Main subscriber for integration.event.

    Auto-dispatches to specific event names based on event data:
    - integration.event.{category}.{event_type} (specific)
    - integration.event.{category} (category-level)

    This allows hooks to register for exactly what they want.
    """
    # Dispatch to specific event name (e.g., integration.event.messaging.send)
    specific_event = event.dispatch_event_name
    if core.has_listeners(specific_event):
        core.dispatch(specific_event, (event,))

    # Dispatch to category-level event (e.g., integration.event.messaging)
    category_event = event.category_event_name
    if core.has_listeners(category_event):
        core.dispatch(category_event, (event,))


def _on_integration_event_span_start(event: "IntegrationEvent", span) -> None:
    """
    Subscriber for integration.event.span_start.

    Called after span creation to allow hooks to enrich the span.
    """
    # Dispatch to specific event name
    specific_event = f"{event.dispatch_event_name}.span_start"
    if core.has_listeners(specific_event):
        core.dispatch(specific_event, (event, span))

    # Dispatch to category-level event
    category_event = f"{event.category_event_name}.span_start"
    if core.has_listeners(category_event):
        core.dispatch(category_event, (event, span))


def _on_integration_event_span_finish(event: "IntegrationEvent", span) -> None:
    """
    Subscriber for integration.event.span_finish.

    Called before span finishes to allow final processing.
    """
    # Dispatch to specific event name
    specific_event = f"{event.dispatch_event_name}.span_finish"
    if core.has_listeners(specific_event):
        core.dispatch(specific_event, (event, span))

    # Dispatch to category-level event
    category_event = f"{event.category_event_name}.span_finish"
    if core.has_listeners(category_event):
        core.dispatch(category_event, (event, span))


def register_subscriber() -> None:
    """
    Register the integration event subscriber with core dispatch.

    This should be called once during ddtrace initialization.
    """
    global _subscriber_registered
    if _subscriber_registered:
        return

    core.on("integration.event", _on_integration_event)
    core.on("integration.event.span_start", _on_integration_event_span_start)
    core.on("integration.event.span_finish", _on_integration_event_span_finish)

    _subscriber_registered = True


def unregister_subscriber() -> None:
    """
    Unregister the integration event subscriber.

    Primarily used for testing.
    """
    global _subscriber_registered
    if not _subscriber_registered:
        return

    core.reset_listeners("integration.event", _on_integration_event)
    core.reset_listeners("integration.event.span_start", _on_integration_event_span_start)
    core.reset_listeners("integration.event.span_finish", _on_integration_event_span_finish)

    _subscriber_registered = False
