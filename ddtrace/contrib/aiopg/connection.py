import asyncio
import wrapt

from aiopg.utils import _ContextManager

from .. import dbapi
from ...ext import sql

from ddtrace import Pin


class AIOTracedCursor(wrapt.ObjectProxy):
    """ TracedCursor wraps a psql cursor and traces it's queries. """

    def __init__(self, cursor, pin):
        super(AIOTracedCursor, self).__init__(cursor)
        pin.onto(self)

    @asyncio.coroutine
    def _trace_method(self, method, query, extra_tags, *args, **kwargs):
        pin = Pin.get_from(self)
        if not pin or not pin.enabled():
            result = yield from method(*args, **kwargs)  # noqa: E999
            return result
        service = pin.service

        name = (pin.app or 'sql') + "." + method.__name__
        with pin.tracer.trace(name, service=service) as s:
            s.span_type = sql.TYPE
            s.set_tag(sql.QUERY, query or self.query.decode('utf-8'))
            s.set_tags(pin.tags)
            s.set_tags(extra_tags)

            try:
                result = yield from method(*args, **kwargs)
                return result
            finally:
                s.set_metric("db.rowcount", self.rowcount)

    @asyncio.coroutine
    def executemany(self, operation, *args, **kwargs):
        # FIXME[matt] properly handle kwargs here. arg names can be different
        # with different libs.
        result = yield from self._trace_method(
            self.__wrapped__.executemany, operation, {'sql.executemany': 'true'},
            operation, *args, **kwargs)  # noqa: E999
        return result

    @asyncio.coroutine
    def execute(self, operation, *args, **kwargs):
        result = yield from self._trace_method(
            self.__wrapped__.execute, operation, {}, operation, *args, **kwargs)
        return result

    @asyncio.coroutine
    def callproc(self, procname, args):
        result = yield from self._trace_method(
            self.__wrapped__.callproc, procname, {}, procname, args)  # noqa: E999
        return result

    @asyncio.coroutine
    def mogrify(self, operation, parameters=None):
        result = yield from self._trace_method(
            self.__wrapped__.mogrify, operation, {}, operation, parameters)  # noqa: E999
        return result

    @asyncio.coroutine
    def fetchone(self):
        result = yield from self._trace_method(
            self.__wrapped__.fetchone, None, {})  # noqa: E999
        return result

    @asyncio.coroutine
    def fetchmany(self, size=None):
        result = yield from self._trace_method(
            self.__wrapped__.fetchmany, None, {}, size)  # noqa: E999
        return result

    @asyncio.coroutine
    def fetchall(self):
        result = yield from self._trace_method(
            self.__wrapped__.fetchall, None, {})  # noqa: E999
        return result

    @asyncio.coroutine
    def scroll(self, value, mode="relative"):
        result = yield from self._trace_method(
            self.__wrapped__.scroll, None, {}, value, mode)  # noqa: E999
        return result

    @asyncio.coroutine
    def nextset(self):
        result = yield from self._trace_method(
            self.__wrapped__.nextset, None, {})  # noqa: E999
        return result

    def __aiter__(self):
        return self.__wrapped__.__aiter__()

    @asyncio.coroutine
    def __anext__(self):
        result = yield from self.__wrapped__.__anext__()
        return result


class AIOTracedConnection(wrapt.ObjectProxy):
    """ TracedConnection wraps a Connection with tracing code. """

    def __init__(self, conn, pin):
        super(AIOTracedConnection, self).__init__(conn)
        pin.onto(self)

    def cursor(self, *args, **kwargs):
        # unfortunately we also need to patch this method as otherwise "self"
        # ends up being the aiopg connection object
        self._last_usage = self._loop.time()
        coro = self._cursor(*args, **kwargs)
        return _ContextManager(coro)

    @asyncio.coroutine
    def _cursor(self, *args, **kwargs):
        cursor = yield from self.__wrapped__._cursor(*args, **kwargs)  # noqa: E999
        pin = Pin.get_from(self)
        if not pin:
            return cursor
        return AIOTracedCursor(cursor, pin)
