import asyncio

import wrapt
from aiopg.utils import _ContextManager
from contrib import dbapi
from ddtrace import Pin
from ext import sql


class AIOTracedCursor(wrapt.ObjectProxy):
    """ TracedCursor wraps a psql cursor and traces it's queries. """

    _datadog_pin = None
    _datadog_name = None

    def __init__(self, cursor, pin):
        super(AIOTracedCursor, self).__init__(cursor)
        self._datadog_pin = pin
        name = pin.app or 'sql'
        self._datadog_name = '%s.query' % name

    @asyncio.coroutine
    def _trace_method(self, method, resource, extra_tags, *args, **kwargs):
        pin = self._datadog_pin
        if not pin or not pin.enabled():
            result = yield from method(*args, **kwargs)  # noqa: E999
            return result
        service = pin.service

        with pin.tracer.trace(self._datadog_name, service=service,
                              resource=resource) as s:
            s.span_type = sql.TYPE
            s.set_tag(sql.QUERY, resource)
            s.set_tags(pin.tags)

            for k, v in extra_tags.items():
                s.set_tag(k, v)

            try:
                result = yield from method(*args, **kwargs)
                return result
            finally:
                s.set_metric("db.rowcount", self.rowcount)

    @asyncio.coroutine
    def executemany(self, query, *args, **kwargs):
        # FIXME[matt] properly handle kwargs here. arg names can be different
        # with different libs.
        result = yield from self._trace_method(
            self.__wrapped__.executemany, query, {'sql.executemany': 'true'},
            query, *args, **kwargs)  # noqa: E999
        return result

    @asyncio.coroutine
    def execute(self, query, *args, **kwargs):
        result = yield from self._trace_method(
            self.__wrapped__.execute, query, {}, query, *args, **kwargs)
        return result

    @asyncio.coroutine
    def callproc(self, proc, args):
        result = yield from self._trace_method(
            self.__wrapped__.callproc, proc, {}, proc, args)  # noqa: E999
        return result

    # aiopg doesn't support __enter__/__exit__ however we're adding it here to
    # support unittests with both styles
    def __enter__(self):
        return self

    def __exit__(self, exc_type, exc_val, exc_tb):
        self.__wrapped__.close()


class AIOTracedConnection(wrapt.ObjectProxy):
    """ TracedConnection wraps a Connection with tracing code. """

    _datadog_pin = None

    def __init__(self, conn):
        super(AIOTracedConnection, self).__init__(conn)
        name = dbapi._get_vendor(conn)
        Pin(service=name, app=name).onto(self)

    def cursor(self, *args, **kwargs):
        # unfortunately we also need to patch this method as otherwise "self"
        # ends up being the aiopg connection object
        coro = self._cursor(*args, **kwargs)
        return _ContextManager(coro)

    @asyncio.coroutine
    def _cursor(self, *args, **kwargs):
        cursor = yield from self.__wrapped__._cursor(*args, **kwargs)  # noqa: E999
        pin = self._datadog_pin
        if not pin:
            return cursor
        return AIOTracedCursor(cursor, pin)