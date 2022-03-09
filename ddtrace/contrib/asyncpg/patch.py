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
    with pin.tracer.trace(
        "postgres.connect", span_type=SpanTypes.SQL, service=ext_service(pin, config.asyncpg)
    ) as span:
        conn = _TracedConnection(await func(*args, **kwargs), pin)
        span.set_tags(_get_connection_tags(conn))
        return conn


@with_traced_module_sync
def _traced_connection_cursor(asyncpg, pin, func, instance, args, kwargs):
    cursor = func(*args, **kwargs)
    cur_pin = pin.clone()
    cur_pin.onto(cursor)
    return cursor


@with_traced_module
async def _traced_cursor_exec(asyncpg, pin, func, instance, args, kwargs):
    with pin.tracer.trace("postgres.cursor.execute"):
        return await func(*args, **kwargs)


def _patch(asyncpg):
    # type: (ModuleType) -> None
    wrap(asyncpg, "connect", _traced_connect(asyncpg))
    wrap(asyncpg, "Connection.cursor", _traced_connection_cursor(asyncpg))
    wrap(asyncpg.cursor, "Cursor._exec", _traced_cursor_exec(asyncpg))


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
    unwrap(asyncpg.cursor.Cursor, "_exec")


def unpatch():
    # type: () -> None
    import asyncpg

    if not getattr(asyncpg, "_datadog_patch", False):
        return

    _unpatch(asyncpg)

    setattr(asyncpg, "_datadog_patch", False)
