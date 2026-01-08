"""
HTTP client instrumentation base class.

HTTPClientInstrumentation provides common functionality for HTTP client
integrations, including event creation helpers and URL parsing.
"""

from typing import Callable
from typing import Dict
from typing import Optional
from urllib.parse import urlparse

from ddtrace._trace.integrations.events import HTTPClientEvent
from ddtrace.contrib.v2._base import InstrumentationPlugin


class HTTPClientInstrumentation(InstrumentationPlugin):
    """
    Base instrumentation for HTTP clients.

    Provides:
    - URL parsing utilities
    - Header injection for distributed tracing
    - Response status handling
    """

    def create_http_client_event(
        self,
        method: str,
        url: str,
        headers: Optional[Dict[str, str]] = None,
        inject_callback: Optional[Callable[[Dict[str, str]], None]] = None,
        **extra_tags,
    ) -> HTTPClientEvent:
        """Helper to create an HTTPClientEvent with common fields."""
        parsed = urlparse(url)

        return HTTPClientEvent(
            _span_name=f"{self.name}.request",
            _resource=f"{method} {parsed.path or '/'}",
            _integration_name=self.name,
            component=self.name,
            http_method=method,
            http_url=url,
            http_target=parsed.path,
            network_destination_name=parsed.hostname,
            network_destination_port=parsed.port,
            _request_headers=headers or {},
            _inject_headers_callback=inject_callback,
            **extra_tags,
        )
