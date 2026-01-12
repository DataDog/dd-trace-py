"""
Integration-specific event classes.

These events extend base events with integration-specific fields.
"""

from ddtrace._trace.integrations.events.contrib.messaging import KafkaEvent
from ddtrace._trace.integrations.events.contrib.messaging import MessagingEvent


__all__ = [
    "MessagingEvent",
    "KafkaEvent",
]
