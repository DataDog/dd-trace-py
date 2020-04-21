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

    async def _trace_method(self, method, query, extra_tags, *args, **kwargs):
        pin = Pin.get_from(self)
        if not pin or not pin.enabled():
            result = await method(*args, **kwargs)
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
                result = await method(*args, **kwargs)
                return result
            finally:
                s.set_metric('db.rowcount', self.rowcount)

    async def executemany(self, operation, *args, **kwargs):
        # FIXME[matt] properly handle kwargs here. arg names can be different
        # with different libs.
        result = await self._trace_method(
            self.__wrapped__.executemany, operation, {'sql.executemany': 'true'},
            operation, *args, **kwargs)
        return result

    async def execute(self, operation, *args, **kwargs):
        result = await self._trace_method(
            self.__wrapped__.execute, operation, {}, operation, *args, **kwargs)
        return result

    async def callproc(self, procname, args):
        result = await self._trace_method(
            self.__wrapped__.callproc, procname, {}, procname, args)
        return result

    async def mogrify(self, operation, parameters=None):
        result = await self._trace_method(
            self.__wrapped__.mogrify, operation, {}, operation, parameters)
        return result

    async def fetchone(self):
        result = await self._trace_method(
            self.__wrapped__.fetchone, None, {})
        return result

    async def fetchmany(self, size=None):
        result = await self._trace_method(
            self.__wrapped__.fetchmany, None, {}, size)
        return result

    async def fetchall(self):
        result = await self._trace_method(
            self.__wrapped__.fetchall, None, {})
        return result

    async def scroll(self, value, mode='relative'):
        result = await self._trace_method(
            self.__wrapped__.scroll, None, {}, value, mode)
        return result

    async def nextset(self):
        result = await self._trace_method(
            self.__wrapped__.nextset, None, {})
        return result

    def __aiter__(self):
        return self.__wrapped__.__aiter__()

    async def __anext__(self):
        result = await self.__wrapped__.__anext__()
        return result

    if AIOPG_1X:
        async def __aenter__(self):
            await self.__wrapped__.__aenter__()
            return self

        async def __aexit__(self, exc_type, exc_val, exc_tb):
            await self.__wrapped__.__aexit__(exc_type, exc_val, exc_tb)


class AIOTracedConnection(wrapt.ObjectProxy):
    """ TracedConnection wraps a Connection with tracing code. """

    def __init__(self, conn, pin=None):
        super(AIOTracedConnection, self).__init__(conn)
        name = dbapi._get_vendor(conn)
        db_pin = pin or Pin(service=name, app=name)
        db_pin.onto(self)

    def cursor(self, *args, **kwargs):
        if hasattr(self, 'close_cursor'):
            self.close_cursor()

        # unfortunately we also need to patch this method as otherwise "self"
        # ends up being the aiopg connection object
        self._last_usage = self._loop.time()  # set like wrapped class
        coro = self._cursor(*args, **kwargs)
        return _ContextManager(coro)

    if AIOPG_1X:
        # This cannot be the old style generator
        async def _cursor(self, *args, **kwargs):
            cursor = await self.__wrapped__._cursor(*args, **kwargs)
            pin = Pin.get_from(self)
            if not pin:
                return cursor

            cursor = AIOTracedCursor(cursor, pin)
            if hasattr(self, '_cursor_instance'):
                self._cursor_instance = cursor

            return cursor
    else:
        @asyncio.coroutine  # here for aiopg < 1.0
        def _cursor(self, *args, **kwargs):
            cursor = yield from self.__wrapped__._cursor(*args, **kwargs)
            pin = Pin.get_from(self)
            if not pin:
                return cursor

            return AIOTracedCursor(cursor, pin)

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

        async def __aexit__(self, exc_type, exc_val, exc_tb):
            await self.__wrapped__.__aexit__(exc_type, exc_val, exc_tb)
