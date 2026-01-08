"""
Base handler classes for span event processing.

These are the category-level handlers that process base events.
"""

from ddtrace._trace.integrations.handlers.base._base import SpanEventHandler
from ddtrace._trace.integrations.handlers.base.database import DatabaseEventHandler
from ddtrace._trace.integrations.handlers.base.http_client import HTTPClientEventHandler
from ddtrace._trace.integrations.handlers.base.http_server import HTTPServerEventHandler
from ddtrace._trace.integrations.handlers.base.messaging import MessagingEventHandler


__all__ = [
    "SpanEventHandler",
    "DatabaseEventHandler",
    "HTTPClientEventHandler",
    "HTTPServerEventHandler",
    "MessagingEventHandler",
]
