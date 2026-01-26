"""
Integration event - the payload dispatched for all integration events.
"""

from dataclasses import dataclass
from dataclasses import field
from typing import Any
from typing import Callable
from typing import Dict
from typing import Optional


@dataclass
class SpanConfig:
    """
    Configuration for span creation.

    This tells the dispatcher how to create the span for this event.
    """

    name: str
    service: Optional[str] = None
    resource: Optional[str] = None
    span_type: Optional[str] = None

    # Optional: span start time in nanoseconds (for cases where function
    # must execute before span creation, e.g., consumer extracting headers)
    start_ns: Optional[int] = None

    # Optional: function to compute parent context
    # Signature: (event: IntegrationEvent) -> Optional[Context]
    parent_context_getter: Optional[Callable[["IntegrationEvent"], Any]] = None


@dataclass
class IntegrationEvent:
    """
    Event payload for integration.event dispatch.

    Contains all facts emitted by the patch. Patches should only populate
    this with data - no tracer or span logic.

    Attributes:
        integration: Descriptor with integration metadata
        event_type: The type of event (e.g., "send", "consume", "request")
        span_config: Configuration for span creation
        payload: Integration-specific data (topic, headers, etc.)
        extracted_context: Parent context from propagation (set by hooks)
    """

    # Import here to avoid circular imports
    from .descriptor import IntegrationDescriptor

    integration: IntegrationDescriptor
    event_type: str
    span_config: SpanConfig
    payload: Dict[str, Any] = field(default_factory=dict)

    # Mutable state set by hooks
    extracted_context: Optional[Any] = None

    # The span created for this event (set by dispatcher)
    span: Optional[Any] = None

    def get(self, key: str, default: Any = None) -> Any:
        """Get a value from the payload."""
        return self.payload.get(key, default)

    def set(self, key: str, value: Any) -> None:
        """Set a value in the payload."""
        self.payload[key] = value

    @property
    def dispatch_event_name(self) -> str:
        """
        Generate the specific event name for core.dispatch.

        Format: integration.event.{category}.{event_type}
        Example: integration.event.messaging.send
        """
        return f"integration.event.{self.integration.category}.{self.event_type}"

    @property
    def category_event_name(self) -> str:
        """
        Generate the category-level event name for core.dispatch.

        Format: integration.event.{category}
        Example: integration.event.messaging
        """
        return f"integration.event.{self.integration.category}"
