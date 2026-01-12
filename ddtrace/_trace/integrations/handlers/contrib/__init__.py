"""
Integration-specific handler classes.

These handlers extend base handlers with integration-specific logic.
"""

from ddtrace._trace.integrations.handlers.contrib.kafka import KafkaEventHandler


__all__ = [
    "KafkaEventHandler",
]
