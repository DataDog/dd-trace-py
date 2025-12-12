"""
Base tracing plugin classes.

This module provides the hierarchical plugin system for tracing integrations.
"""

from ddtrace._trace.tracing_plugins.base.client import ClientPlugin
from ddtrace._trace.tracing_plugins.base.consumer import ConsumerPlugin
from ddtrace._trace.tracing_plugins.base.database import DatabasePlugin
from ddtrace._trace.tracing_plugins.base.inbound import InboundPlugin
from ddtrace._trace.tracing_plugins.base.outbound import OutboundPlugin
from ddtrace._trace.tracing_plugins.base.producer import ProducerPlugin
from ddtrace._trace.tracing_plugins.base.server import ServerPlugin
from ddtrace._trace.tracing_plugins.base.storage import StoragePlugin
from ddtrace._trace.tracing_plugins.base.tracing import TracingPlugin


__all__ = [
    "TracingPlugin",
    "OutboundPlugin",
    "ClientPlugin",
    "StoragePlugin",
    "DatabasePlugin",
    "InboundPlugin",
    "ServerPlugin",
    "ProducerPlugin",
    "ConsumerPlugin",
]
