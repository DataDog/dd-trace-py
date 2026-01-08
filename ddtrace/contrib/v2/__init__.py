"""
V2 instrumentation framework.

Instrumentation base classes for integration patching using the new
event-based architecture.
"""

from ddtrace.contrib.v2._base import InstrumentationPlugin
from ddtrace.contrib.v2.database import DatabaseInstrumentation
from ddtrace.contrib.v2.http_client import HTTPClientInstrumentation
from ddtrace.contrib.v2.http_server import WebFrameworkInstrumentation
from ddtrace.contrib.v2.messaging import MessagingInstrumentation


__all__ = [
    "InstrumentationPlugin",
    "DatabaseInstrumentation",
    "HTTPClientInstrumentation",
    "WebFrameworkInstrumentation",
    "MessagingInstrumentation",
]
