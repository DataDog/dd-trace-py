import os
from typing import TYPE_CHECKING

from ddtrace import Pin
from ddtrace import config
from ddtrace.vendor import wrapt

from ...constants import SPAN_MEASURED_KEY
from ...ext import SpanTypes
from ...ext import db
from ...ext import net
from ...internal.logger import get_logger
from ...internal.utils import get_argument_value
from ...internal.utils.formats import asbool
from ..trace_utils import ext_service
from ..trace_utils import unwrap
from ..trace_utils import with_traced_module as with_traced_module_sync
from ..trace_utils import wrap
from ..trace_utils_async import with_traced_module


if TYPE_CHECKING:
    from types import ModuleType
    from typing import Dict

    import asyncpg


config._add(
    "asyncpg",
    dict(
        trace_fetch_methods=asbool(os.getenv("DD_ASYNCPG_TRACE_FETCH_METHODS", default=False)),
        _default_service="postgres",
    ),
)


log = get_logger(__name__)


def _get_connection_tags(conn):
    # type: (asyncpg.Connection) -> Dict[str, str]
    addr = conn._addr
    params = conn._params
    host = port = ""
    if isinstance(addr, tuple) and len(addr) == 2:
        host, port = addr
    return {
        net.TARGET_HOST: host,
        net.TARGET_PORT: port,
        db.USER: params.user,
        db.NAME: params.database,
    }


class _TracedConnection(wrapt.ObjectProxy):
    def __init__(self, conn, pin):
        super(_TracedConnection, self).__init__(conn)
        pin.onto(self)

    async def _traced_method(self, method, query, args, kwargs):
        pin = Pin.get_from(self)
        if not pin or not pin.enabled:
            log.debug("No pin or pin disabled, skipping tracing for %s", method)
            return await method(*args, **kwargs)

        with pin.tracer.trace(
            "postgres.query", resource=query, service=ext_service(pin, config.asyncpg), span_type=SpanTypes.SQL
        ) as span:
            span.set_tag(SPAN_MEASURED_KEY)
            span.set_tags(_get_connection_tags(self))
            return await method(*args, **kwargs)

    async def execute(self, *args, **kwargs):
        query = get_argument_value(args, kwargs, 0, "query")
        return await self._traced_method(self.__wrapped__.execute, query, args, kwargs)

    async def executemany(self, *args, **kwargs):
        command = get_argument_value(args, kwargs, 0, "command")
        return await self._traced_method(self.__wrapped__.executemany, command, args, kwargs)

    async def fetch(self, *args, **kwargs):
        query = get_argument_value(args, kwargs, 0, "query")
        return await self._traced_method(self.__wrapped__.fetch, query, args, kwargs)

    async def fetchval(self, *args, **kwargs):
        query = get_argument_value(args, kwargs, 0, "query")
        return await self._traced_method(self.__wrapped__.fetchval, query, args, kwargs)

    async def fetchrow(self, *args, **kwargs):
        query = get_argument_value(args, kwargs, 0, "query")
        return await self._traced_method(self.__wrapped__.fetchrow, query, args, kwargs)


@with_traced_module
async def _traced_connect(asyncpg, pin, func, instance, args, kwargs):
    """Traced asyncpg.connect().

    connect() is instrumented and patched to return a connection proxy.
    """
    with pin.tracer.trace(
        "postgres.connect", span_type=SpanTypes.SQL, service=ext_service(pin, config.asyncpg)
    ) as span:
        conn = _TracedConnection(await func(*args, **kwargs), pin)
        span.set_tags(_get_connection_tags(conn))
        return conn


class _TracedCursorFactory(wrapt.ObjectProxy):
    def __init__(self, cursor, pin):
        super(_TracedCursorFactory, self).__init__(cursor)
        pin.onto(self)

    def __aiter__(self):
        # ObjectProxy doesn't support aiter so need to proxy manually.
        return self.__wrapped__.__aiter__()

    def __await__(self):
        pin = Pin.get_from(self)
        if not pin or not pin.enabled:
            log.debug("No pin or pin disabled, skipping tracing")
            return self.__wrapped__.__await__()

        with pin.tracer.trace(
            "postgres.cursor", resource=self._query, span_type=SpanTypes.SQL, service=ext_service(pin, config.asyncpg)
        ):
            return self.__wrapped__.__await__()


@with_traced_module_sync
def _traced_connection_cursor(asyncpg, pin, func, instance, args, kwargs):
    """Traced asyncpg.Connection.cursor()

    Trace cursor(), which returns a CursorFactory to return a proxy
    cursor factory.
    """
    return _TracedCursorFactory(func(*args, **kwargs), pin)


class _TracedCursor(wrapt.ObjectProxy):
    def __init__(self, cursor, pin):
        super(_TracedCursor, self).__init__(cursor)
        pin.onto(self)

    async def _traced_method(self, method, args, kwargs):
        pin = Pin.get_from(self)
        if not pin or not pin.enabled:
            log.debug("No pin or pin disabled, skipping tracing for %s", method)
            return await method(*args, **kwargs)

        with pin.tracer.trace(
            "postgres.query", service=ext_service(pin, config.asyncpg), span_type=SpanTypes.SQL
        ) as span:
            span.set_tag(SPAN_MEASURED_KEY)
            span.set_tags(_get_connection_tags(self))
            return await method(*args, **kwargs)

    async def fetch(self, *args, **kwargs):
        return await self._traced_method(self.__wrapped__.fetch, *args, **kwargs)

    async def fetchrow(self, *args, **kwargs):
        return await self._traced_method(self.__wrapped__.fetch, *args, **kwargs)


@with_traced_module
async def _traced_cursor_init(asyncpg, pin, func, instance, args, kwargs):
    """Traced asyncpg.cursor.Cursor._init

    The _init method is used to create Cursor objects. It is patched
    to return a proxy cursor.
    """
    cursor = await func(*args, **kwargs)
    if config.asyncpg.trace_fetch_methods:
        return _TracedCursor(cursor, pin)
    return cursor


def _patch(asyncpg):
    # type: (ModuleType) -> None
    wrap(asyncpg, "connect", _traced_connect(asyncpg))
    wrap(asyncpg, "Connection.cursor", _traced_connection_cursor(asyncpg))
    wrap(asyncpg.cursor, "Cursor._init", _traced_cursor_init(asyncpg))


def patch():
    # type: () -> None
    import asyncpg

    if getattr(asyncpg, "_datadog_patch", False):
        return

    Pin().onto(asyncpg)
    _patch(asyncpg)

    setattr(asyncpg, "_datadog_patch", True)


def _unpatch(asyncpg):
    # type: (ModuleType) -> None
    unwrap(asyncpg, "connect")
    unwrap(asyncpg.Connection, "cursor")
    unwrap(asyncpg.cursor.Cursor, "_init")


def unpatch():
    # type: () -> None
    import asyncpg

    if not getattr(asyncpg, "_datadog_patch", False):
        return

    _unpatch(asyncpg)

    setattr(asyncpg, "_datadog_patch", False)
