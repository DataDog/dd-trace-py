"""
Messaging instrumentation base class.

MessagingInstrumentation provides common functionality for message broker
integrations, including event creation helpers for produce/consume operations.
"""

from typing import Callable
from typing import Dict
from typing import Optional

from ddtrace._trace.integrations.events import MessagingEvent
from ddtrace.contrib.v2._base import InstrumentationPlugin


class MessagingInstrumentation(InstrumentationPlugin):
    """
    Base instrumentation for message brokers.

    Provides:
    - Producer/consumer span creation
    - Message header injection/extraction
    - Batch message handling
    """

    messaging_system: str = ""  # "kafka", "rabbitmq", "sqs", etc.

    def create_producer_event(
        self,
        destination: str,
        key: Optional[str] = None,
        inject_callback: Optional[Callable[[Dict[str, str]], None]] = None,
        **extra_tags,
    ) -> MessagingEvent:
        """Helper to create a produce event."""
        return MessagingEvent(
            _span_name=f"{self.messaging_system}.produce",
            _resource=destination,
            _integration_name=self.name,
            component=self.name,
            span_kind="producer",
            messaging_system=self.messaging_system,
            messaging_destination_name=destination,
            messaging_operation="publish",
            _inject_headers_callback=inject_callback,
            **extra_tags,
        )

    def create_consumer_event(
        self,
        destination: str,
        headers: Optional[Dict[str, str]] = None,
        **extra_tags,
    ) -> MessagingEvent:
        """Helper to create a consume event."""
        return MessagingEvent(
            _span_name=f"{self.messaging_system}.consume",
            _resource=destination,
            _integration_name=self.name,
            component=self.name,
            span_kind="consumer",
            messaging_system=self.messaging_system,
            messaging_destination_name=destination,
            messaging_operation="receive",
            _propagation_headers=headers or {},
            **extra_tags,
        )
