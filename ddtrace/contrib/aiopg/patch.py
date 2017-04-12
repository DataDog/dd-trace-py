import asyncio

# 3p
import aiopg.connection
import wrapt

from ddtrace.contrib import dbapi
from ddtrace.contrib.psycopg.patch import _patch_extensions, patch_conn as psycppg_patch_conn
from ddtrace.ext import sql


# Original connect method, we don't want the _ContextManager
_connect = aiopg.connection._connect


class AIOTracedCursor(dbapi.TracedCursor):
    @asyncio.coroutine
    def _trace_method(self, method, resource, extra_tags, *args, **kwargs):
        pin = self._datadog_pin
        if not pin or not pin.enabled():
            result = yield from method(*args, **kwargs)
            return result
        service = pin.service

        with pin.tracer.trace(self._datadog_name, service=service, resource=resource) as s:
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
        result = yield from self._trace_method(self.__wrapped__.executemany, query, {'sql.executemany': 'true'}, query, *args, **kwargs)
        return result

    @asyncio.coroutine
    def execute(self, query, *args, **kwargs):
        result = yield from self._trace_method(self.__wrapped__.execute, query, {}, query, *args, **kwargs)
        return result

    @asyncio.coroutine
    def callproc(self, proc, args):
        result = yield from self._trace_method(self.__wrapped__.callproc, proc, {}, proc, args)
        return result


class AIOTracedConnection(dbapi.TracedConnection):
    @asyncio.coroutine
    def _cursor(self, *args, **kwargs):
        cursor = yield from self.__wrapped__._cursor(*args, **kwargs)
        pin = self._datadog_pin
        if not pin:
            return cursor
        return AIOTracedCursor(cursor, pin)



def patch():
    """ Patch monkey patches psycopg's connection function
        so that the connection's functions are traced.
    """
    wrapt.wrap_function_wrapper(aiopg, 'connect', patched_connect)
    _patch_extensions() # do this early just in case

def unpatch():
    aiopg.connection._connect = _connect

def patched_connect(connect_func, _, args, kwargs):
    conn = connect_func(*args, **kwargs)
    return psycppg_patch_conn(conn, traced_conn_cls=AIOTracedConnection)