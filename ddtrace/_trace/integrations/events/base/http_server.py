"""
HTTP server event for inbound HTTP request tracing.

HTTPServerEvent contains all fields needed to trace inbound HTTP requests
handled by web frameworks (Flask, Django, FastAPI, etc.).
"""

from dataclasses import dataclass
from dataclasses import field
from typing import Dict
from typing import Optional

from ddtrace._trace.integrations.events.base._base import SpanEvent


@dataclass
class HTTPServerEvent(SpanEvent):
    """
    Event for inbound HTTP requests (web frameworks).
    """

    _span_type: str = "web"
    span_kind: str = "server"
    instrumentation_category: str = "http_server"

    # HTTP request
    http_method: Optional[str] = None
    http_url: Optional[str] = None
    http_route: Optional[str] = None
    http_target: Optional[str] = None
    http_user_agent: Optional[str] = None
    http_status_code: Optional[int] = None  # set on finish

    # Client info
    http_client_ip: Optional[str] = None
    net_peer_ip: Optional[str] = None

    # Internal use (not tags)
    _request_headers: Dict[str, str] = field(default_factory=dict)
