"""
HTTP server (web framework) instrumentation base class.

WebFrameworkInstrumentation provides common functionality for web framework
integrations, including event creation helpers and request parsing.
"""

from typing import Dict
from typing import Optional

from ddtrace._trace.integrations.events import HTTPServerEvent
from ddtrace.contrib.v2._base import InstrumentationPlugin


class WebFrameworkInstrumentation(InstrumentationPlugin):
    """
    Base instrumentation for web frameworks.

    Provides:
    - Request span creation
    - Distributed tracing context extraction
    - Response status handling
    - Route/endpoint extraction patterns
    """

    def create_request_event(
        self,
        method: str,
        url: str,
        path: str,
        headers: Optional[Dict[str, str]] = None,
        client_ip: Optional[str] = None,
        **extra_tags,
    ) -> HTTPServerEvent:
        """Helper to create a request event."""
        return HTTPServerEvent(
            _span_name=f"{self.name}.request",
            _resource=f"{method} {path}",
            _integration_name=self.name,
            component=self.name,
            http_method=method,
            http_url=url,
            http_target=path,
            http_client_ip=client_ip,
            _request_headers=headers or {},
            **extra_tags,
        )
