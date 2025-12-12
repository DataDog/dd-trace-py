"""
InboundPlugin - Base for incoming connections.

This plugin handles connections FROM external sources:
- Web framework requests
- Message queue consumers
- gRPC servers

Provides distributed tracing context extraction.
"""

from typing import TYPE_CHECKING
from typing import Any
from typing import Optional

from ddtrace._trace.tracing_plugins.base.tracing import TracingPlugin


if TYPE_CHECKING:
    from ddtrace.internal.core import ExecutionContext


class InboundPlugin(TracingPlugin):
    """
    Base plugin for inbound connections.

    Connections FROM external sources:
    - HTTP server requests
    - gRPC server calls
    - Message queue consumers
    - WebSocket connections

    Provides:
    - Distributed tracing context extraction
    - Parent store binding for context propagation
    """

    type = "inbound"

    def extract_distributed_context(
        self,
        ctx: "ExecutionContext",
        headers: dict,
    ) -> Optional[Any]:
        """
        Extract distributed tracing context from headers.

        Args:
            ctx: The execution context
            headers: Dict-like object containing trace headers

        Returns:
            Extracted context or None
        """
        from ddtrace.propagation.http import HTTPPropagator

        return HTTPPropagator.extract(headers)

    def activate_distributed_context(
        self,
        ctx: "ExecutionContext",
        headers: dict,
    ) -> None:
        """
        Extract and activate distributed tracing context.

        This sets up the trace context from incoming headers so that
        spans created during request handling are part of the distributed trace.

        Args:
            ctx: The execution context
            headers: Dict-like object containing trace headers
        """
        from ddtrace.contrib.internal.trace_utils import activate_distributed_headers

        pin = ctx.get_item("pin")
        if not pin:
            return

        activate_distributed_headers(
            pin.tracer,
            int_config=self.integration_config,
            request_headers=headers,
        )
