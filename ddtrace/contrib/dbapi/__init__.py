"""
Generic dbapi tracing code.
"""

# stdlib
import logging

# 3p
import wrapt

# project
from ddtrace import Pin
from ddtrace.ext import sql


log = logging.getLogger(__name__)


class TracedCursor(wrapt.ObjectProxy):
    """ TracedCursor wraps a psql cursor and traces it's queries. """

    _datadog_pin = None
    _datadog_name = None

    def __init__(self, cursor, pin):
        super(TracedCursor, self).__init__(cursor)
        self._datadog_pin = pin
        name = pin.app or 'sql'
        self._datadog_name = '%s.query' % name

    def executemany(self, query, *args, **kwargs):
        pin = self._datadog_pin
        if not pin or not pin.enabled():
            return self.__wrapped__.executemany(query, *args, **kwargs)
        service = pin.service

        # FIXME[matt] properly handle kwargs here. arg names can be different
        # with different libs.
        with pin.tracer.trace(self._datadog_name, service=service, resource=query) as s:
            s.span_type = sql.TYPE
            s.set_tag(sql.QUERY, query)
            s.set_tags(pin.tags)
            s.set_tag("sql.executemany", "true")
            try:
                return self.__wrapped__.executemany(query, *args, **kwargs)
            finally:
                s.set_metric("db.rowcount", self.rowcount)

    def execute(self, query, *args, **kwargs):
        pin = self._datadog_pin
        if not pin or not pin.enabled():
            return self.__wrapped__.execute(query, *args, **kwargs)

        service = pin.service
        with pin.tracer.trace(self._datadog_name, service=service, resource=query) as s:
            s.span_type = sql.TYPE
            s.set_tag(sql.QUERY, query)
            s.set_tags(pin.tags)
            try:
                return self.__wrapped__.execute(query, *args, **kwargs)
            finally:
                s.set_metric("db.rowcount", self.rowcount)

    def callproc(self, proc, args):
        pin = self._datadog_pin
        if not pin or not pin.enabled():
            return self.__wrapped__.callproc(proc, args)

        with pin.tracer.trace(self._datadog_name, service=pin.service, resource=proc) as s:
            s.span_type = sql.TYPE
            s.set_tag(sql.QUERY, proc)
            s.set_tags(pin.tags)
            try:
                return self.__wrapped__.callproc(proc, args)
            finally:
                s.set_metric("db.rowcount", self.rowcount)


class TracedConnection(wrapt.ObjectProxy):
    """ TracedConnection wraps a Connection with tracing code. """

    _datadog_pin = None

    def __init__(self, conn):
        super(TracedConnection, self).__init__(conn)
        name = _get_vendor(conn)
        Pin(service=name, app=name).onto(self)

    def cursor(self, *args, **kwargs):
        cursor = self.__wrapped__.cursor(*args, **kwargs)
        pin = self._datadog_pin
        if not pin:
            return cursor
        return TracedCursor(cursor, pin)


def _get_vendor(conn):
    """ Return the vendor (e.g postgres, mysql) of the given
        database.
    """
    try:
        name = _get_module_name(conn)
    except Exception:
        log.debug("couldnt parse module name", exc_info=True)
        name = "sql"
    return sql.normalize_vendor(name)

def _get_module_name(conn):
    return conn.__class__.__module__.split('.')[0]
