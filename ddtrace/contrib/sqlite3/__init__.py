
import functools

from sqlite3 import Connection, Cursor
from tracer.ext import sql as sqlx


def connection_factory(tracer, service="sqlite3"):
    """ Return a connection factory class that will can be used to trace
        sqlite queries.

        >>> factory = connection_factor(my_tracer, service="my_db_service")
        >>> conn = sqlite3.connect(":memory:", factory=factory)
    """
    return functools.partial(TracedConnection,
        datadog_tracer=tracer,
        datadog_service=service,
    )


class TracedCursor(Cursor):
    """ A cursor base class that will trace sql queries. """

    def __init__(self, *args, **kwargs):
        self._datadog_tracer = kwargs.pop("datadog_tracer", None)
        self._datadog_service = kwargs.pop("datadog_service", None)
        Cursor.__init__(self, *args, **kwargs)

    def execute(self, sql, *args, **kwargs):
        if not self._datadog_tracer:
            return Cursor.execute(self, sql, *args, **kwargs)

        with self._datadog_tracer.trace("sqlite3.query", span_type=sqlx.TYPE) as s:
            s.set_tag(sqlx.QUERY, sql)
            s.service = self._datadog_service
            s.resource = sql # will be normalized
            return Cursor.execute(self, sql, *args, **kwargs)


class TracedConnection(Connection):
    """ A cursor base class that will trace sql queries. """

    def __init__(self, *args, **kwargs):
        self._datadog_tracer = kwargs.pop("datadog_tracer", None)
        self._datadog_service = kwargs.pop("datadog_service", None)
        Connection.__init__(self, *args, **kwargs)

        self._datadog_cursor_class = functools.partial(TracedCursor,
            datadog_tracer=self._datadog_tracer,
            datadog_service=self._datadog_service,
        )

    def cursor(self, *args, **kwargs):
        if self._datadog_tracer:
            kwargs.setdefault('factory', self._datadog_cursor_class)
        return Connection.cursor(self, *args, **kwargs)

