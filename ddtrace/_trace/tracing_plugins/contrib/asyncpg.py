"""
asyncpg integration plugin.

This module provides tracing plugins for asyncpg (async PostgreSQL driver).

Plugins:
- AsyncpgExecutePlugin: Handles asyncpg.execute events (queries)
- AsyncpgConnectPlugin: Handles asyncpg.connect events (connections)

These plugins extend DatabasePlugin and extract all necessary data from
the raw (instance, args, kwargs) passed by the instrumentor.
"""

from typing import TYPE_CHECKING
from typing import Any
from typing import Dict
from typing import Optional

from ddtrace._trace.tracing_plugins.base.database import DatabasePlugin


if TYPE_CHECKING:
    from ddtrace.internal.core import ExecutionContext


class AsyncpgExecutePlugin(DatabasePlugin):
    """
    Handles asyncpg.execute events.

    Subscribes to: context.started.asyncpg.execute
                   context.ended.asyncpg.execute

    Extracts:
    - Query from args (first positional argument or 'state' kwarg)
    - Connection info from protocol instance._connection

    The DatabasePlugin base class handles:
    - Span creation with proper naming
    - Database tags (db.system, db.name, etc.)
    - DBM propagation
    - Rowcount tracking
    - Error handling
    """

    @property
    def package(self) -> str:
        return "asyncpg"

    @property
    def operation(self) -> str:
        return "execute"

    # PostgreSQL system identifiers
    system: Optional[str] = "postgresql"
    db_system: Optional[str] = "postgresql"

    def on_start(self, ctx: "ExecutionContext") -> None:
        """Extract data from raw objects and create span."""
        from ddtrace._trace.tracing_plugins.base.events import DatabaseContext

        pin = ctx.get_item("pin")
        if not pin or not pin.enabled():
            return

        # Extract from raw objects passed by instrumentor
        instance = ctx.get_item("instance")
        args = ctx.get_item("args", ())
        kwargs = ctx.get_item("kwargs", {})

        # Extract query
        query = self._extract_query(args, kwargs)

        # Extract connection info from protocol instance
        conn_info = self._extract_connection_info(instance)

        # Build context and set on execution context
        db_ctx = DatabaseContext(
            db_system=self.db_system or "postgresql",
            query=query,
            host=conn_info.get("host"),
            port=conn_info.get("port"),
            user=conn_info.get("user"),
            database=conn_info.get("database"),
        )

        ctx.set_item("db_context", db_ctx)
        ctx.set_item("resource", query)

        # Let parent create span
        super().on_start(ctx)

    def _extract_query(self, args: tuple, kwargs: dict) -> Optional[str]:
        """Extract query from execute arguments."""
        state = args[0] if args else kwargs.get("state")
        if isinstance(state, (str, bytes)):
            return state if isinstance(state, str) else state.decode("utf-8", errors="replace")
        # PreparedStatement - get the query attribute
        return getattr(state, "query", None)

    def _extract_connection_info(self, instance: Any) -> Dict[str, Any]:
        """Extract connection info from Protocol instance."""
        conn = getattr(instance, "_connection", None)
        if not conn:
            return {}

        addr = getattr(conn, "_addr", None)
        params = getattr(conn, "_params", None)

        result: Dict[str, Any] = {}
        if addr and isinstance(addr, tuple) and len(addr) >= 2:
            result["host"] = addr[0]
            result["port"] = addr[1]
        if params:
            result["user"] = getattr(params, "user", None)
            result["database"] = getattr(params, "database", None)

        return result


class AsyncpgConnectPlugin(DatabasePlugin):
    """
    Handles asyncpg.connect events.

    Subscribes to: context.started.asyncpg.connect
                   context.ended.asyncpg.connect

    Extracts connection parameters from kwargs passed to connect().
    """

    @property
    def package(self) -> str:
        return "asyncpg"

    @property
    def operation(self) -> str:
        return "connect"

    # PostgreSQL system identifiers
    system: Optional[str] = "postgresql"
    db_system: Optional[str] = "postgresql"

    def on_start(self, ctx: "ExecutionContext") -> None:
        """Extract connection info and create span."""
        from ddtrace._trace.tracing_plugins.base.events import DatabaseContext

        pin = ctx.get_item("pin")
        if not pin or not pin.enabled():
            return

        # Extract from raw objects passed by instrumentor
        args = ctx.get_item("args", ())
        kwargs = ctx.get_item("kwargs", {})

        # Extract connection params from connect() arguments
        # asyncpg.connect(dsn=None, host=None, port=None, user=None, database=None, ...)
        host = kwargs.get("host") or (args[1] if len(args) > 1 else None)
        port = kwargs.get("port") or (args[2] if len(args) > 2 else None)
        user = kwargs.get("user") or (args[3] if len(args) > 3 else None)
        database = kwargs.get("database") or (args[4] if len(args) > 4 else None)

        # Build context
        db_ctx = DatabaseContext(
            db_system=self.db_system or "postgresql",
            query=None,  # connect has no query
            host=host,
            port=port,
            user=user,
            database=database,
        )

        ctx.set_item("db_context", db_ctx)
        ctx.set_item("resource", "connect")

        # Let parent create span
        super().on_start(ctx)

    def _get_span_name(self) -> str:
        """Override to use connect-specific span name."""
        return "postgres.connect"
