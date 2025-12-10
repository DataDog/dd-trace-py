"""
ClientPlugin - Base for client-side operations.

Extends OutboundPlugin for client-type operations like HTTP requests.
This is the base for HTTP clients, gRPC clients, etc.
"""

from ddtrace._trace.tracing_plugins.base.outbound import OutboundPlugin
from ddtrace.ext import SpanKind


class ClientPlugin(OutboundPlugin):
    """
    Base plugin for client-side operations.

    Extends OutboundPlugin with:
    - kind: CLIENT (span.kind = "client")
    - type: "web" by default

    Use this as the base for:
    - HTTP clients (httpx, requests, aiohttp client)
    - gRPC clients
    - Other RPC clients
    """

    kind = SpanKind.CLIENT
    type = "web"
