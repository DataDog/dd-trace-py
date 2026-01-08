"""
Event structs for integration tracing.

These dataclasses define the contracts between instrumentation code (producers)
and trace handlers (consumers). Field names directly become span tags.

Event Hierarchy:
    SpanEvent (base)
    ├── NetworkingEvent (outbound network operations)
    │   ├── DatabaseEvent
    │   ├── HTTPClientEvent
    │   └── MessagingEvent
    │       └── KafkaEvent
    └── HTTPServerEvent (inbound requests)
"""

from ddtrace._trace.integrations.events.base import DatabaseEvent
from ddtrace._trace.integrations.events.base import HTTPClientEvent
from ddtrace._trace.integrations.events.base import HTTPServerEvent
from ddtrace._trace.integrations.events.base import NetworkingEvent
from ddtrace._trace.integrations.events.base import SpanEvent
from ddtrace._trace.integrations.events.contrib import KafkaEvent
from ddtrace._trace.integrations.events.contrib import MessagingEvent


__all__ = [
    "SpanEvent",
    "NetworkingEvent",
    "DatabaseEvent",
    "HTTPClientEvent",
    "HTTPServerEvent",
    "MessagingEvent",
    "KafkaEvent",
]
