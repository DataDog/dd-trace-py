"""
Database instrumentation base class.

DatabaseInstrumentation provides common functionality for database client
integrations, including event creation helpers and DBM propagation.
"""

from typing import Optional

from ddtrace._trace.integrations.events import DatabaseEvent
from ddtrace.contrib.v2._base import InstrumentationPlugin


class DatabaseInstrumentation(InstrumentationPlugin):
    """
    Base instrumentation for database clients.

    Provides:
    - Common connection info extraction patterns
    - DBM (Database Monitoring) propagation helpers
    - Query sanitization utilities
    """

    db_system: str = ""  # "postgresql", "mysql", "mongodb", etc.

    def create_database_event(
        self,
        span_name: str,
        query: Optional[str] = None,
        host: Optional[str] = None,
        port: Optional[int] = None,
        database: Optional[str] = None,
        user: Optional[str] = None,
        **extra_tags,
    ) -> DatabaseEvent:
        """Helper to create a DatabaseEvent with common fields."""
        return DatabaseEvent(
            _span_name=span_name,
            _resource=query,
            _integration_name=self.name,
            component=self.name,
            db_system=self.db_system,
            db_statement=query,
            db_name=database,
            db_user=user,
            network_destination_name=host,
            network_destination_port=port,
            **extra_tags,
        )
