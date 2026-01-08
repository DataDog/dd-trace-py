"""
asyncpg integration using the new event-based architecture.

This module provides tracing for asyncpg (async PostgreSQL driver) using:
- DatabaseInstrumentation base class
- DatabaseEvent for event data
- trace_event_async() context manager

Traces:
- Protocol.execute (queries)
- connect (connections)
"""

from typing import Any
from typing import Dict

import asyncpg
import asyncpg.protocol
import wrapt

from ddtrace._trace.integrations.events import DatabaseEvent
from ddtrace._trace.integrations.handlers import trace_event_async
from ddtrace.contrib.v2.database import DatabaseInstrumentation


class AsyncpgInstrumentation(DatabaseInstrumentation):
    """
    asyncpg instrumentation plugin.

    Traces:
    - Protocol.execute (queries)
    - connect (connections)
    """

    name = "asyncpg"
    supported_versions = ">=0.22.0"
    db_system = "postgresql"

    def patch(self) -> None:
        """Apply patches to asyncpg."""
        if not self.is_supported():
            return

        wrapt.wrap_function_wrapper("asyncpg.protocol", "Protocol.execute", self._wrap_execute)
        wrapt.wrap_function_wrapper("asyncpg", "connect", self._wrap_connect)

        self._patched = True

    def unpatch(self) -> None:
        """Remove patches from asyncpg."""
        if hasattr(asyncpg.protocol.Protocol.execute, "__wrapped__"):
            asyncpg.protocol.Protocol.execute = asyncpg.protocol.Protocol.execute.__wrapped__
        if hasattr(asyncpg.connect, "__wrapped__"):
            asyncpg.connect = asyncpg.connect.__wrapped__

        self._patched = False

    async def _wrap_execute(self, wrapped, instance, args, kwargs):
        """Wrap Protocol.execute to trace queries."""
        event = self._create_execute_event(instance, args, kwargs)

        async with trace_event_async(event) as span:  # noqa: F841
            result = await wrapped(*args, **kwargs)

            # Update row count on finish
            if hasattr(result, "__len__"):
                event.db_row_count = len(result)

            return result

    async def _wrap_connect(self, wrapped, instance, args, kwargs):
        """Wrap connect to trace connection creation."""
        event = self._create_connect_event(args, kwargs)

        async with trace_event_async(event) as span:  # noqa: F841
            return await wrapped(*args, **kwargs)

    def _create_execute_event(self, instance: Any, args: tuple, kwargs: Dict[str, Any]) -> DatabaseEvent:
        """Extract context from Protocol.execute()."""
        # Extract query
        state = args[0] if args else kwargs.get("state")
        if isinstance(state, bytes):
            query = state.decode("utf-8", errors="replace")
        elif isinstance(state, str):
            query = state
        else:
            query = getattr(state, "query", None)

        # Extract connection info
        conn = getattr(instance, "_connection", None)
        addr = getattr(conn, "_addr", None) if conn else None
        params = getattr(conn, "_params", None) if conn else None

        host = addr[0] if addr and len(addr) >= 2 else None
        port = addr[1] if addr and len(addr) >= 2 else None

        return self.create_database_event(
            span_name="postgres.query",
            query=query,
            host=host,
            port=port,
            database=getattr(params, "database", None) if params else None,
            user=getattr(params, "user", None) if params else None,
        )

    def _create_connect_event(self, args: tuple, kwargs: Dict[str, Any]) -> DatabaseEvent:
        """Extract context from connect()."""
        host = kwargs.get("host") or (args[1] if len(args) > 1 else None)
        port = kwargs.get("port") or (args[2] if len(args) > 2 else None)
        user = kwargs.get("user") or (args[3] if len(args) > 3 else None)
        database = kwargs.get("database") or (args[4] if len(args) > 4 else None)

        return self.create_database_event(
            span_name="postgres.connect",
            host=host,
            port=port,
            database=database,
            user=user,
        )


# Singleton instance for use by the patch system
asyncpg_instrumentation = AsyncpgInstrumentation()
