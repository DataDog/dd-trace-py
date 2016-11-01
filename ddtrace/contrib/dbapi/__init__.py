
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
    _tags = None

    def __init__(self, cursor, tracer, service, name, tags):
        super(TracedCursor, self).__init__(cursor)
        self._service = service
        self._tracer = tracer or ddtrace.tracer
        self._name = name
        self._tags = tags

    def execute(self, query, *args, **kwargs):
        if not self._tracer.enabled:
            return self.__wrapped__.execute(*args, **kwargs)

        with self._tracer.trace(self._name, service=self._service, resource=query) as s:
            s.span_type = sql.TYPE
            s.set_tag(sql.QUERY, query)
            if self._tags:
                s.set_tags(self._tags)
            try:
                return self.__wrapped__.execute(query, *args, **kwargs)
            finally:
                s.set_metric("db.rowcount", self.rowcount)


class TracedConnection(wrapt.ObjectProxy):
    """ TracedConnection wraps a Connection with tracing code. """

    datadog_service = None
    datadog_name = None
    datadog_tracer = None
    datadog_tags = None

    def __init__(self, conn, name=None):
        super(TracedConnection, self).__init__(conn)
        if name is None:
            try:
                module = _get_module_name(conn)
            except Exception:
                log.warn("couldnt parse module name", exc_info=True)
                module = "sql"
        vendor = sql.normalize_vendor(module)
        self.datadog_service = vendor
        self.datadog_name = "%s.query" % vendor

    def execute(self, *args, **kwargs):
        # this method only exists on some clients, so trigger an attribute
        # error if it doesn't.
        getattr(self.__wrapped__, 'execute')

        # otherwise, keep going.
        cursor = self.cursor()
        return cursor.execute(*args, **kwargs)

    def cursor(self, *args, **kwargs):
        cursor = self.__wrapped__.cursor(*args, **kwargs)
        return TracedCursor(
            cursor=cursor,
            service=self.datadog_service,
            name=self.datadog_name,
            tracer=self.datadog_tracer,
            tags=self.datadog_tags,
        )

def configure(conn, name=None, service=None, tracer=None, tags=None):

    def _set_if(attr, val):
        if hasattr(conn, attr) and val:
            setattr(conn, attr, val)

    _set_if("datadog_service", service)
    _set_if("datadog_tracer", tracer)
    _set_if("datadog_name", name)
    _set_if("datadog_tags", tags)

def _get_module_name(conn):
    return conn.__class__.__module__.split('.')[0]
