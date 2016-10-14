
# stdlib
import logging

# 3p
import wrapt

import ddtrace
from ddtrace.ext import sql


log = logging.getLogger(__name__)


class TracedCursor(wrapt.ObjectProxy):
    """ TracedCursor wraps a psql cursor and traces it's queries. """

    _service = None
    _tracer = None
    _name = None

    def __init__(self, cursor, service, name, tracer):
        super(TracedCursor, self).__init__(cursor)
        self._service = service
        self._tracer = tracer
        self._name = name

    def execute(self, query, *args, **kwargs):
        if not self._tracer.enabled:
            return self.__wrapped__.execute(*args, **kwargs)

        with self._tracer.trace(self._name) as s:
            s.resource = query
            s.service = self._service
            s.span_type = sql.TYPE
            s.set_tag(sql.QUERY, query)
            try:
                return self.__wrapped__.execute(query, *args, **kwargs)
            finally:
                s.set_metric("db.rowcount", self.rowcount)


class TracedConnection(wrapt.ObjectProxy):
    """ TracedConnection wraps a Connection with tracing code. """

    datadog_service = None
    datadog_name = None
    datadog_tracer = None

    def __init__(self, conn, name=None):
        super(TracedConnection, self).__init__(conn)
        if name is None:
            try:
                name = _get_module_name(conn)
            except Exception:
                log.warn("couldnt parse module name", exc_info=True)

        self.datadog_name = "%s.query" % (name or 'sql')

    def cursor(self, *args, **kwargs):
        cursor = self.__wrapped__.cursor(*args, **kwargs)
        return TracedCursor(
            cursor,
            self.datadog_service,
            self.datadog_name,
            self.datadog_tracer or ddtrace.tracer)


def _get_module_name(conn):
    # there must be a better way
    return str(type(conn)).split("'")[1].split('.')[0]
