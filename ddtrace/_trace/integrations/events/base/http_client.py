"""
HTTP client event for outbound HTTP request tracing.

HTTPClientEvent contains all fields needed to trace outbound HTTP requests
made by HTTP client libraries (requests, httpx, aiohttp, etc.).
"""

from dataclasses import dataclass
from dataclasses import field
from typing import Callable
from typing import Dict
from typing import Optional

from ddtrace._trace.integrations.events.base._base import NetworkingEvent


@dataclass
class HTTPClientEvent(NetworkingEvent):
    """
    Event for outbound HTTP requests.
    """

    _span_type: str = "http"
    instrumentation_category: str = "http_client"

    # HTTP request
    http_method: Optional[str] = None
    http_url: Optional[str] = None
    http_target: Optional[str] = None
    http_status_code: Optional[int] = None  # set on finish

    # Internal use (not tags)
    _request_headers: Dict[str, str] = field(default_factory=dict)
    _inject_headers_callback: Optional[Callable[[Dict[str, str]], None]] = None
