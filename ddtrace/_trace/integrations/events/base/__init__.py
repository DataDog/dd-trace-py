"""
Base event classes for integration tracing.

These are the category-level events that integrations extend.
"""

from ddtrace._trace.integrations.events.base._base import NetworkingEvent
from ddtrace._trace.integrations.events.base._base import SpanEvent
from ddtrace._trace.integrations.events.base.database import DatabaseEvent
from ddtrace._trace.integrations.events.base.http_client import HTTPClientEvent
from ddtrace._trace.integrations.events.base.http_server import HTTPServerEvent


__all__ = [
    "SpanEvent",
    "NetworkingEvent",
    "DatabaseEvent",
    "HTTPClientEvent",
    "HTTPServerEvent",
]
