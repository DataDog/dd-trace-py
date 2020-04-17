# stdlib
import asyncio
import pkg_resources

# 3p
import aiopg
from aiopg.utils import _ContextManager

from ddtrace.vendor import wrapt
from .. import dbapi
from ...constants import ANALYTICS_SAMPLE_RATE_KEY, SPAN_MEASURED_KEY
from ...pin import Pin
from ...settings import config
from ddtrace.ext import sql


AIOPG_1X = pkg_resources.parse_version(aiopg.__version__) >= pkg_resources.parse_version('1.0.0')


class AIOTracedCursor(wrapt.ObjectProxy):
    """ TracedCursor wraps a psql cursor and traces its queries. """

    def __init__(self, cursor, pin):
        super(AIOTracedCursor, self).__init__(cursor)
        pin.onto(self)

    @asyncio.coroutine
    def _trace_method(self, method, query, extra_tags, *args, **kwargs):
        pin = Pin.get_from(self)
        if not pin or not pin.enabled():
            result = yield from method(*args, **kwargs)
            return result
        service = pin.service

        name = (pin.app or 'sql') + '.' + method.__name__
        with pin.tracer.trace(name, service=service,
                              resource=query or self.query.decode('utf-8'),
                              span_type=sql.TYPE) as s:
            s.set_tag(SPAN_MEASURED_KEY)
            s.set_tags(pin.tags)
            s.set_tags(extra_tags)

            # set analytics sample rate
            s.set_tag(
                ANALYTICS_SAMPLE_RATE_KEY,
                config.aiopg.get_analytics_sample_rate()
            )

            try:
                result = yield from method(*args, **kwargs)
                return result
            finally:
                s.set_metric('db.rowcount', self.rowcount)

    @asyncio.coroutine
    def executemany(self, operation, *args, **kwargs):
        # FIXME[matt] properly handle kwargs here. arg names can be different
        # with different libs.
        result = yield from self._trace_method(
            self.__wrapped__.executemany, operation, {'sql.executemany': 'true'},
            operation, *args, **kwargs)
        return result

    @asyncio.coroutine
    def execute(self, operation, *args, **kwargs):
        result = yield from self._trace_method(
            self.__wrapped__.execute, operation, {}, operation, *args, **kwargs)
        return result

    @asyncio.coroutine
    def callproc(self, procname, args):
        result = yield from self._trace_method(
            self.__wrapped__.callproc, procname, {}, procname, args)
        return result

    @asyncio.coroutine
    def mogrify(self, operation, parameters=None):
        result = yield from self._trace_method(
            self.__wrapped__.mogrify, operation, {}, operation, parameters)
        return result

    @asyncio.coroutine
    def fetchone(self):
        result = yield from self._trace_method(
            self.__wrapped__.fetchone, None, {})
        return result

    @asyncio.coroutine
    def fetchmany(self, size=None):
        result = yield from self._trace_method(
            self.__wrapped__.fetchmany, None, {}, size)
        return result

    @asyncio.coroutine
    def fetchall(self):
        result = yield from self._trace_method(
            self.__wrapped__.fetchall, None, {})
        return result

    @asyncio.coroutine
    def scroll(self, value, mode='relative'):
        result = yield from self._trace_method(
            self.__wrapped__.scroll, None, {}, value, mode)
        return result

    @asyncio.coroutine
    def nextset(self):
        result = yield from self._trace_method(
            self.__wrapped__.nextset, None, {})
        return result

    def __aiter__(self):
        return self.__wrapped__.__aiter__()

    @asyncio.coroutine
    def __anext__(self):
        result = yield from self.__wrapped__.__anext__()
        return result


class AIOTracedConnection(wrapt.ObjectProxy):
    """ TracedConnection wraps a Connection with tracing code. """

    def __init__(self, conn, pin=None, cursor_cls=AIOTracedCursor):
        super(AIOTracedConnection, self).__init__(conn)
        name = dbapi._get_vendor(conn)
        db_pin = pin or Pin(service=name, app=name)
        db_pin.onto(self)
        # wrapt requires prefix of `_self` for attributes that are only in the
        # proxy (since some of our source objects will use `__slots__`)
        self._self_cursor_cls = cursor_cls

    def cursor(self, *args, **kwargs):
        # unfortunately we also need to patch this method as otherwise "self"
        # ends up being the aiopg connection object
        self._last_usage = self._loop.time()  # set like wrapped class
        coro = self._cursor(*args, **kwargs)
        return _ContextManager(coro)

    @asyncio.coroutine
    def _cursor(self, *args, **kwargs):
        cursor = yield from self.__wrapped__._cursor(*args, **kwargs)
        pin = Pin.get_from(self)
        if not pin:
            return cursor
        return self._self_cursor_cls(cursor, pin)

    if AIOPG_1X:
        async def _await_helper(self):
            pin = Pin.get_from(self)
            name = (pin.app or 'sql') + '.connect'
            with pin.tracer.trace(name, service=pin.service) as s:
                s.span_type = sql.TYPE
                s.set_tags(pin.tags)
                await self.__wrapped__._connect()
            return self

        def __await__(self):
            return self._await_helper().__await__()

        async def __aenter__(self):
            await self.__wrapped__.__aenter__()
            return self
