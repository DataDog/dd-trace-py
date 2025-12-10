"""
DatabasePlugin - Base for all database integrations.

Extends StoragePlugin with database-specific functionality:
- DBM (Database Monitoring) comment propagation
- Query tagging and truncation
- Rowcount tracking
- Standard database tags (db.system, db.name, db.user, etc.)
"""

from typing import TYPE_CHECKING
from typing import Any
from typing import Optional
from typing import Tuple

from ddtrace._trace.tracing_plugins.base.storage import StoragePlugin


if TYPE_CHECKING:
    from ddtrace._trace.span import Span
    from ddtrace._trace.tracing_plugins.base.events import DatabaseContext
    from ddtrace.internal.core import ExecutionContext


class DatabasePlugin(StoragePlugin):
    """
    Base plugin for database integrations.

    Handles all common database tracing:
    - Standard tags (db.system, db.name, db.user, net.target.host, etc.)
    - DBM (Database Monitoring) comment propagation
    - Query resource tagging
    - Rowcount tracking
    - Measured span marking

    Subclasses just need to define:
    - package: str (e.g., "asyncpg")
    - operation: str (e.g., "execute")
    - db_system: str (e.g., "postgresql")
    """

    type = "sql"

    # Override in subclasses
    db_system: Optional[str] = None  # "postgresql", "mysql", "sqlite", "mongodb"

    def on_start(self, ctx: "ExecutionContext") -> None:
        """
        Create database span with standard tags.

        Reads from ctx:
        - pin: The Pin instance
        - db_context: DatabaseContext with query, connection info, etc.
        - span_name: Optional override for span name
        - resource: Optional override for resource name
        """
        from ddtrace.constants import _SPAN_MEASURED_KEY
        from ddtrace.ext import SpanTypes

        pin = ctx.get_item("pin")
        db_ctx: Optional["DatabaseContext"] = ctx.get_item("db_context")

        if not pin or not pin.enabled():
            return

        # Get span name
        span_name = ctx.get_item("span_name") or self._get_span_name()

        # Determine resource (query)
        resource = ctx.get_item("resource")
        if resource is None and db_ctx and db_ctx.query:
            resource = db_ctx.query

        # Create span
        span = self.start_span(
            ctx,
            span_name,
            resource=resource,
            span_type=SpanTypes.SQL,
        )

        if not span:
            return

        # Mark as measured (shows in APM metrics)
        span.set_metric(_SPAN_MEASURED_KEY, 1)

        # Set database tags from context
        if db_ctx:
            self._set_database_tags(span, db_ctx)

        # Set any tags from pin
        if pin.tags:
            span.set_tags(pin.tags)

        # Handle DBM propagation
        if db_ctx and db_ctx.dbm_propagator:
            self._apply_dbm_propagation(ctx, span, db_ctx)

    def on_finish(
        self,
        ctx: "ExecutionContext",
        exc_info: Tuple[Optional[type], Optional[BaseException], Optional[Any]],
    ) -> None:
        """
        Finish span with rowcount and error handling.

        Reads from ctx:
        - rowcount: Optional row count from query result
        """
        from ddtrace.ext import db

        span = ctx.span
        if not span:
            return

        # Set rowcount if available
        rowcount = ctx.get_item("rowcount")
        if rowcount is not None:
            span.set_metric(db.ROWCOUNT, rowcount)
            # Also set as tag for backward compatibility
            if isinstance(rowcount, int) and rowcount >= 0:
                span.set_tag(db.ROWCOUNT, rowcount)

        # Call parent for peer service and error handling
        super().on_finish(ctx, exc_info)

    def _get_span_name(self) -> str:
        """
        Get default span name for this database.

        Uses schema to generate proper span name based on db_system.
        """
        from ddtrace.internal.schema import schematize_database_operation

        system = self.db_system or self.system or "db"
        return schematize_database_operation(
            f"{system}.query",
            database_provider=system,
        )

    def _set_database_tags(self, span: "Span", db_ctx: "DatabaseContext") -> None:
        """
        Set standard database tags from DatabaseContext.

        Tags set:
        - db.system: Database type
        - db.name: Database name
        - db.user: Database user
        - net.target.host: Host
        - net.target.port: Port
        - server.address: Host (alias)
        """
        from ddtrace.ext import db

        # Database system
        system = db_ctx.db_system or self.db_system
        if system:
            span._set_tag_str(db.SYSTEM, system)

        # Connection info
        if db_ctx.host:
            self.add_host(span, db_ctx.host, db_ctx.port)

        if db_ctx.user:
            span._set_tag_str(db.USER, db_ctx.user)

        if db_ctx.database:
            span._set_tag_str(db.NAME, db_ctx.database)

        # Extra tags from context
        if db_ctx.tags:
            span.set_tags(db_ctx.tags)

    def _apply_dbm_propagation(
        self,
        ctx: "ExecutionContext",
        span: "Span",
        db_ctx: "DatabaseContext",
    ) -> None:
        """
        Apply DBM (Database Monitoring) comment injection.

        This injects a SQL comment with trace context into the query,
        allowing correlation between traces and database query analytics.

        The actual injection is handled by a dispatcher that the integration
        can hook into.
        """
        from ddtrace.internal import core

        if not db_ctx.dbm_propagator:
            return

        # Dispatch to integration-specific DBM handler
        # The handler modifies args/kwargs to inject the DBM comment
        result = core.dispatch_with_results(
            f"{self.package}.execute",
            (
                self.integration_config,
                span,
                ctx.get_item("args", ()),
                ctx.get_item("kwargs", {}),
            ),
        ).result

        if result and result.value:
            _, modified_args, modified_kwargs = result.value
            ctx.set_item("modified_args", modified_args)
            ctx.set_item("modified_kwargs", modified_kwargs)

    def _get_peer_service(self, ctx: "ExecutionContext", span: "Span") -> Optional[str]:
        """
        Get peer service for database spans.

        For databases, prefer db.name as the peer service identifier.
        """
        from ddtrace.ext import db

        # Try db.name first for databases
        db_name = span.get_tag(db.NAME)
        if db_name:
            return str(db_name)

        # Fall back to parent implementation
        return super()._get_peer_service(ctx, span)
