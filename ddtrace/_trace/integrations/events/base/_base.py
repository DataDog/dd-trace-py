"""
Base event class for integration tracing.

SpanEvent is the root class for all span events. It defines the core
span attributes and the get_tags() method for extracting tags from fields.

Design principles:
- Fields starting with '_' are span attributes, not tags
- All other fields become span tags directly (field name = tag name)
"""

from dataclasses import dataclass
from typing import Any
from typing import Dict
from typing import Optional


@dataclass
class SpanEvent:
    """
    Base class for all span events.

    Contains core span attributes that all integrations share.
    Fields starting with '_' are span attributes (not tags).
    All other fields become span tags directly.
    """

    # --- Core span attributes (not tags, prefixed with _) ---
    _span_name: str  # Operation name (e.g., "postgres.query")
    _resource: Optional[str] = None  # Resource name (e.g., "SELECT * FROM users")
    _service: Optional[str] = None  # Service name override
    _span_type: Optional[str] = None  # Span type (sql, web, http, worker, etc.)
    _integration_name: str = ""  # Integration name for config lookup

    # --- Standard tags (field name = tag name) ---
    component: Optional[str] = None  # component tag
    span_kind: Optional[str] = None  # span.kind tag
    instrumentation_category: Optional[str] = None  # Category: database, http_client, http_server, messaging

    def get_tags(self) -> Dict[str, Any]:
        """
        Return all public fields as tags.

        Fields starting with '_' are span attributes, not tags.
        """
        return {k: v for k, v in self.__dict__.items() if not k.startswith("_") and v is not None}


@dataclass
class NetworkingEvent(SpanEvent):
    """
    Base class for outbound networking events.

    Extended by DatabaseEvent, MessagingEvent, HTTPClientEvent, etc.
    Contains common networking destination fields.
    """

    span_kind: str = "client"

    # Networking destination (common to all outbound network operations)
    network_destination_name: Optional[str] = None  # Host/address
    network_destination_port: Optional[int] = None  # Port
