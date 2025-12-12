"""
ServerPlugin - Base for server-side request handling.

Extends InboundPlugin for server operations (HTTP servers, gRPC servers, etc.)
"""

from ddtrace._trace.tracing_plugins.base.inbound import InboundPlugin
from ddtrace.ext import SpanKind


class ServerPlugin(InboundPlugin):
    """
    Base plugin for server-side request handling.

    Extends InboundPlugin with:
    - kind: SERVER (span.kind = "server")
    - type: "web"

    Use this as the base for:
    - Web framework servers (Flask, Django, FastAPI)
    - gRPC servers
    - WebSocket servers
    """

    kind = SpanKind.SERVER
    type = "web"
