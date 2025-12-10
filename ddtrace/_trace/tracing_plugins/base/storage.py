"""
StoragePlugin - Base for storage system clients.

Extends ClientPlugin for storage backends like databases and caches.
Provides system-based service naming.
"""

from typing import TYPE_CHECKING
from typing import Any
from typing import Optional

from ddtrace._trace.tracing_plugins.base.client import ClientPlugin


if TYPE_CHECKING:
    from ddtrace._trace.span import Span
    from ddtrace.internal.core import ExecutionContext


class StoragePlugin(ClientPlugin):
    """
    Base plugin for storage system clients.

    Extends ClientPlugin with:
    - type: "storage"
    - System-based service naming: {tracer_service}-{system}

    Use this as the base for:
    - Databases (through DatabasePlugin)
    - Caches (Redis, Memcached)
    - Object stores
    """

    type = "storage"

    # Override in subclasses to set the storage system name
    # e.g., "postgresql", "redis", "memcached"
    system: Optional[str] = None

    def start_span(
        self,
        ctx: "ExecutionContext",
        name: str,
        **options: Any,
    ) -> Optional["Span"]:
        """
        Create span with system-based service naming.

        If service is not specified and system is set, uses the
        integration config's service resolution.
        """
        # Use default service resolution if not specified
        if "service" not in options:
            service = self.get_service(ctx)
            if service:
                options["service"] = service

        return super().start_span(ctx, name, **options)
