"""
Tracing utilities for the psycopg potgres client library.
"""

# stdlib
import functools
import logging

from ...ext import net
from ...ext import sql as sqlx

# 3p
from psycopg2.extensions import connection, cursor


log = logging.getLogger(__name__)


def connection_factory(tracer, service="postgres"):
    """ Return a connection factory class that will can be used to trace
        sqlite queries.

        >>> factory = connection_factor(my_tracer, service="my_db_service")
        >>> conn = pyscopg2.connect(..., connection_factory=factory)
    """
    return functools.partial(TracedConnection,
        datadog_tracer=tracer,
        datadog_service=service,
    )



class TracedCursor(cursor):
    """Wrapper around cursor creating one span per query"""

    def __init__(self, *args, **kwargs):
        self._datadog_tracer = kwargs.pop("datadog_tracer", None)
        self._datadog_service = kwargs.pop("datadog_service", None)
        self._datadog_tags = kwargs.pop("datadog_tags", None)
        super(TracedCursor, self).__init__(*args, **kwargs)

    def execute(self, query, vars=None):
        """ just wrap the cursor execution in a span """
        if not self._datadog_tracer:
            return cursor.execute(self, query, vars)

        with self._datadog_tracer.trace("postgres.query") as s:
            s.resource = query
            s.service = self._datadog_service
            s.span_type = sqlx.TYPE
            s.set_tag(sqlx.QUERY, query)
            s.set_tags(self._datadog_tags)
            try:
                return super(TracedCursor, self).execute(query, vars)
            finally:
                s.set_tag("db.rowcount", self.rowcount)

    def callproc(self, procname, vars=None):
        """ just wrap the execution in a span """
        return cursor.callproc(self, procname, vars)


class TracedConnection(connection):
    """Wrapper around psycopg2  for tracing"""

    def __init__(self, *args, **kwargs):

        self._datadog_tracer = kwargs.pop("datadog_tracer", None)
        self._datadog_service = kwargs.pop("datadog_service", None)

        super(TracedConnection, self).__init__(*args, **kwargs)

        # add metadata (from the connection, string, etc)
        dsn = _parse_dsn(self.dsn)
        self._datadog_tags = {
            net.TARGET_HOST: dsn.get("host"),
            net.TARGET_PORT: dsn.get("port"),
            "db.name": dsn.get("dbname"),
            "db.user": dsn.get("user"),
            "db.application" : dsn.get("application_name"),
        }

        self._datadog_cursor_class = functools.partial(TracedCursor,
            datadog_tracer=self._datadog_tracer,
            datadog_service=self._datadog_service,
            datadog_tags=self._datadog_tags,
        )

        # DogTrace.register_service(
        #     service=self._dogtrace_service,
        #     app="postgres",
        #     app_type="sql",
        # )

    def cursor(self, *args, **kwargs):
        """ register our custom cursor factory """
        kwargs.setdefault('cursor_factory', self._datadog_cursor_class)
        return super(TracedConnection, self).cursor(*args, **kwargs)


def _parse_dsn(dsn):
    """
    Return a diciontary of the components of a postgres DSN.

    >>> _parse_dsn('user=dog port=1543 dbname=dogdata')
    {"user":"dog", "port":"1543", "dbname":"dogdata"}
    """
    # FIXME: replace by psycopg2.extensions.parse_dsn when available
    # https://github.com/psycopg/psycopg2/pull/321
    return {chunk.split("=")[0]: chunk.split("=")[1] for chunk in dsn.split() if "=" in chunk}



