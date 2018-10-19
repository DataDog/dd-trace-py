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

from ...ext import AppTypes

log = logging.getLogger(__name__)


class TracedCursor(wrapt.ObjectProxy):
    """ TracedCursor wraps a psql cursor and traces it's queries. """

    def __init__(self, cursor, pin):
        super(TracedCursor, self).__init__(cursor)
        pin.onto(self)
        name = pin.app or 'sql'
        self._self_datadog_name = '%s.query' % name
        self._self_last_execute_operation = None
        # self._self_last_execute_span = None

    def _trace_method(self, method, name, resource, extra_tags, *args, **kwargs):
        pin = Pin.get_from(self)
        if not pin or not pin.enabled():
            return method(*args, **kwargs)
        service = pin.service
        with pin.tracer.trace(name, service=service, resource=resource) as s:
            s.span_type = sql.TYPE
            s.set_tags(pin.tags)
            s.set_tags(extra_tags)
            # Necessary for django
            s.set_tag(sql.QUERY, resource)

            try:
                return method(*args, **kwargs)
            finally:
                row_count = self.rowcount
                s.set_metric("db.rowcount", row_count)
                # Necessary for django
                if row_count and row_count >= 0:
                    s.set_tag(sql.ROWS, row_count)

    def executemany(self, query, *args, **kwargs):
        self._self_last_execute_operation = None
        # FIXME[matt] properly handle kwargs here. arg names can be different
        # with different libs.
        return self._trace_method(
            self.__wrapped__.executemany, self._self_datadog_name, query, {'sql.executemany': 'true'},
            query, *args, **kwargs)

    def execute(self, query, *args, **kwargs):
        self._self_last_execute_operation = query
        self._trace_method(self.__wrapped__.execute, self._self_datadog_name, query, {}, query, *args, **kwargs)
        return self

    def fetchone(self, *args, **kwargs):
        span_name = "{}.{}".format(self._self_datadog_name, 'fetchone')
        return self._trace_method(self.__wrapped__.fetchone, span_name, self._self_last_execute_operation, {},
                                  *args, **kwargs)

    def fetchall(self, *args, **kwargs):
        span_name = "{}.{}".format(self._self_datadog_name, 'fetchall')
        return self._trace_method(self.__wrapped__.fetchall, span_name, self._self_last_execute_operation, {},
                                  *args, **kwargs)

    def fetchmany(self, *args, **kwargs):
        span_name = "{}.{}".format(self._self_datadog_name, 'fetchmany')
        # We want to trace the information about how many rows were requested. Note that this number may be larger
        # the number of rows actually returned if less then requested are available from the query.
        size_tag_key = 'db.fetch.size'
        if 'size' in kwargs:
            extra_tags = {size_tag_key: kwargs.get('size')}
        elif len(args) == 1 and isinstance(args[0], int):
            extra_tags = {size_tag_key: args[0]}
        else:
            default_array_size = getattr(self.__wrapped__, 'arraysize', None)
            extra_tags = {size_tag_key: default_array_size} if default_array_size else {}

        return self._trace_method(self.__wrapped__.fetchmany, span_name, self._self_last_execute_operation, extra_tags,
                                  *args, **kwargs)

    def callproc(self, proc, args):
        return self._trace_method(self.__wrapped__.callproc, self._self_datadog_name, proc, {}, proc,
                                  args)

    def __enter__(self):
        # previous versions of the dbapi didn't support context managers. let's
        # reference the func that would be called to ensure that errors
        # messages will be the same.
        self.__wrapped__.__enter__

        # and finally, yield the traced cursor.
        return self


class TracedConnection(wrapt.ObjectProxy):
    """ TracedConnection wraps a Connection with tracing code. """

    def __init__(self, conn, pin=None):
        super(TracedConnection, self).__init__(conn)
        name = _get_vendor(conn)
        db_pin = pin or Pin(service=name, app=name, app_type=AppTypes.db)
        db_pin.onto(self)

    def cursor(self, *args, **kwargs):
        cursor = self.__wrapped__.cursor(*args, **kwargs)
        pin = Pin.get_from(self)
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
