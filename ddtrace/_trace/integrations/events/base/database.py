"""
Database event for database integration tracing.

DatabaseEvent contains all fields needed to trace database operations
like queries, connections, and transactions.
"""

from dataclasses import dataclass
from typing import Any
from typing import Optional

from ddtrace._trace.integrations.events.base._base import NetworkingEvent


@dataclass
class DatabaseEvent(NetworkingEvent):
    """
    Event for database operations.

    Field names follow OTel semantic conventions.
    """

    _span_type: str = "sql"
    instrumentation_category: str = "database"

    # Database identification
    db_system: Optional[str] = None  # postgresql, mysql, mongodb, etc.
    db_name: Optional[str] = None  # database name
    db_user: Optional[str] = None
    db_statement: Optional[str] = None  # the query
    db_operation: Optional[str] = None  # SELECT, INSERT, etc.
    db_row_count: Optional[int] = None  # set on finish

    # DBM (Database Monitoring) - internal use, not tags
    _dbm_propagator: Optional[Any] = None
