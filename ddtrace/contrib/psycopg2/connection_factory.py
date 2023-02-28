import functools

from psycopg2.extensions import connection as connection_psycopg2
from psycopg2.extensions import cursor as cursor_psycopg2
from psycopg import cursor as cursor_psycopg

from ddtrace.constants import SPAN_MEASURED_KEY
from ddtrace.ext import SpanTypes
from ddtrace.ext import db
from ddtrace.ext import net
from ddtrace.ext import sql
from ddtrace.internal.constants import COMPONENT


class TracedCursorFactory:
    def __init__(self, cursor_class, config):
        self._config = config
        self._cursor = cursor_class

        class TracedCursor(cursor_class):
            """Wrapper around cursor creating one span per query"""

            def __init__(self, *args, **kwargs):
                self._datadog_tracer = kwargs.pop("datadog_tracer", None)
                self._datadog_service = kwargs.pop("datadog_service", None)
                self._datadog_tags = kwargs.pop("datadog_tags", None)
                self._cursor = cursor_class
                super(TracedCursor, self).__init__(*args, **kwargs)

            def execute(self, query, vars=None):  # noqa: A002
                """just wrap the cursor execution in a span"""
                if not self._datadog_tracer:
                    return self._cursor.execute(self, query, vars)

                with self._datadog_tracer.trace(
                    "postgres.query", service=self._datadog_service, span_type=SpanTypes.SQL
                ) as s:
                    s.set_tag_str(COMPONENT, self._config.integration_name)

                    s.set_tag(SPAN_MEASURED_KEY)
                    if not s.sampled:
                        return super(TracedCursor, self).execute(query, vars)

                    s.resource = query
                    s.set_tags(self._datadog_tags)
                    try:
                        return super(TracedCursor, self).execute(query, vars)
                    finally:
                        s.set_metric(db.ROWCOUNT, self.rowcount)

            def callproc(self, procname, vars=None):  # noqa: A002
                """just wrap the execution in a span"""
                return self._cursor.callproc(self, procname, vars)

        self.traced_cursor = TracedCursor


class TracedConnectionFactory:
    def __init__(self, connection_class, config):
        self._config = config
        self._connection = connection_class
        self._cursor = cursor_psycopg2 if isinstance(connection_class, connection_psycopg2) else cursor_psycopg
        self.traced_cursor = TracedCursorFactory(self._cursor, self._config).traced_cursor

        class TracedConnection(connection_class):
            """Wrapper around psycopg2  for tracing"""

            def __init__(self, *args, **kwargs):
                self._datadog_tracer = kwargs.pop("datadog_tracer", None)
                self._datadog_service = kwargs.pop("datadog_service", None)

                super(TracedConnection, self).__init__(*args, **kwargs)

                # add metadata (from the connection, string, etc)
                dsn = sql.parse_pg_dsn(self.dsn)
                print("connection_factory; ", dsn)
                self._datadog_tags = {
                    net.TARGET_HOST: dsn.get("host"),
                    net.TARGET_PORT: dsn.get("port", 5432),
                    db.NAME: dsn.get("dbname"),
                    db.USER: dsn.get("user"),
                    "db.application": dsn.get("application_name"),
                    db.SYSTEM: self._config.dbms_name,
                }

                self._datadog_cursor_class = functools.partial(
                    self.traced_cursor,
                    datadog_tracer=self._datadog_tracer,
                    datadog_service=self._datadog_service,
                    datadog_tags=self._datadog_tags,
                )

            def cursor(self, *args, **kwargs):
                """register our custom cursor factory"""
                kwargs.setdefault("cursor_factory", self._datadog_cursor_class)
                return super(TracedConnection, self).cursor(*args, **kwargs)

        self.traced_connection = TracedConnection
