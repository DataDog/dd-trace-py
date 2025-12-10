"""
OutboundPlugin - Base for all outgoing connections to external services.

This plugin handles:
- Peer service resolution and tagging
- Host/port tagging
- Span finishing with peer service metadata

Extend this for any integration that connects TO an external service
(databases, HTTP clients, message producers, external APIs, etc.)
"""

from typing import TYPE_CHECKING
from typing import Any
from typing import Optional
from typing import Tuple

from ddtrace._trace.tracing_plugins.base.tracing import TracingPlugin


if TYPE_CHECKING:
    from ddtrace._trace.span import Span
    from ddtrace.internal.core import ExecutionContext


class OutboundPlugin(TracingPlugin):
    """
    Base plugin for outbound connections.

    Connections TO external services:
    - Database queries
    - HTTP client requests
    - Message queue producers
    - External API calls

    Provides:
    - Peer service resolution
    - Host/port tagging utilities
    - Automatic peer service tagging on span finish
    """

    type = "outbound"

    def on_finish(
        self,
        ctx: "ExecutionContext",
        exc_info: Tuple[Optional[type], Optional[BaseException], Optional[Any]],
    ) -> None:
        """
        Finish span with peer service tagging.

        Sets peer.service tag if not already set, then calls parent finish.
        """
        span = ctx.span
        if not span:
            return

        # Set peer service if not already set
        self._set_peer_service(ctx, span)

        # Call parent to handle error and finish
        super().on_finish(ctx, exc_info)

    def _set_peer_service(self, ctx: "ExecutionContext", span: "Span") -> None:
        """
        Determine and set peer.service tag.

        Only sets if peer.service is not already present.
        """
        from ddtrace import config

        # Skip if peer service computation is disabled
        if not getattr(config, "_peer_service_computation_enabled", True):
            return

        # Skip if already set
        if span.get_tag("peer.service"):
            return

        # Try to derive from standard tags
        peer_service = self._get_peer_service(ctx, span)
        if peer_service:
            span._set_tag_str("peer.service", peer_service)

            # Check for peer service remapping
            remapped = self._get_peer_service_remap(peer_service)
            if remapped and remapped != peer_service:
                span._set_tag_str("peer.service", remapped)
                span._set_tag_str("_dd.peer.service.source", "peer.service")

    def _get_peer_service(self, ctx: "ExecutionContext", span: "Span") -> Optional[str]:
        """
        Get peer service from available tags.

        Priority:
        1. net.peer.name
        2. out.host
        3. net.target.host / server.address
        4. db.name (for databases)
        5. messaging.destination (for messaging)

        Override in subclasses for custom logic.
        """
        from ddtrace.ext import net

        # Check standard network tags in priority order
        precursor_tags = [
            "net.peer.name",
            "out.host",
            net.TARGET_HOST,
            net.SERVER_ADDRESS,
            "db.name",
            "messaging.destination.name",
        ]

        for tag in precursor_tags:
            value = span.get_tag(tag)
            if value:
                return str(value)

        return None

    def _get_peer_service_remap(self, peer_service: str) -> Optional[str]:
        """
        Apply peer service remapping if configured.

        Checks DD_TRACE_PEER_SERVICE_MAPPING for overrides.
        """
        from ddtrace import config

        mapping = getattr(config, "_peer_service_mapping", None)
        if mapping and peer_service in mapping:
            return mapping[peer_service]

        return peer_service

    def add_host(
        self,
        span: "Span",
        host: Optional[str],
        port: Optional[int] = None,
    ) -> None:
        """
        Tag span with host and port information.

        Sets standard network tags:
        - net.target.host
        - server.address
        - net.target.port

        Args:
            span: The span to tag
            host: Hostname or IP address
            port: Port number (optional)
        """
        from ddtrace.ext import net

        if host:
            span._set_tag_str(net.TARGET_HOST, host)
            span._set_tag_str(net.SERVER_ADDRESS, host)

        if port is not None:
            span.set_metric(net.TARGET_PORT, port)
