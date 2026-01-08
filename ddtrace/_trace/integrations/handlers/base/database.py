"""
Database event handler.

DatabaseEventHandler processes DatabaseEvent instances, adding:
- DBM (Database Monitoring) comment propagation
- Peer service resolution for database connections
"""

from typing import TYPE_CHECKING
from typing import List

from ddtrace import config
from ddtrace._trace.integrations.events.base.database import DatabaseEvent
from ddtrace._trace.integrations.handlers.base._base import SpanEventHandler


if TYPE_CHECKING:
    from ddtrace._trace.span import Span


class DatabaseEventHandler(SpanEventHandler[DatabaseEvent]):
    """
    Handler for database events.

    Adds:
    - DBM (Database Monitoring) comment propagation
    - Peer service resolution for database connections
    """

    # Priority order for peer service resolution
    peer_service_tags: List[str] = ["db_name", "network_destination_name"]

    def create_span(self, event: DatabaseEvent) -> "Span":
        """Create span and handle DBM injection."""
        span = super().create_span(event)
        self._handle_dbm(span, event)
        return span

    def on_finish(self, span: "Span", event: DatabaseEvent) -> None:
        """Finish with peer service tagging."""
        super().on_finish(span, event)
        self._set_peer_service(span, event)

    def _handle_dbm(self, span: "Span", event: DatabaseEvent) -> None:
        """Inject DBM trace context into query comments."""
        if event._dbm_propagator and event.db_statement:
            event._dbm_propagator.inject(span, event)

    def _set_peer_service(self, span: "Span", event: DatabaseEvent) -> None:
        """Set peer.service from database connection info."""
        if not getattr(config, "_peer_service_computation_enabled", True):
            return
        if span.get_tag("peer.service"):
            return

        for tag in self.peer_service_tags:
            value = span.get_tag(tag)
            if value:
                span._set_tag_str("peer.service", str(value))
                break
