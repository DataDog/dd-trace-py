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
from ..trace_utils import wrap
from ..trace_utils_async import with_traced_module


if TYPE_CHECKING:
    from types import ModuleType
    from typing import Dict
    from typing import Union

    import asyncpg
    from asyncpg.prepared_stmt import PreparedStatement


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
        conn_pin = pin.clone(tags=_get_connection_tags(conn))
        # Keep the pin on the protocol
        conn_pin.onto(self._protocol)

    def __setddpin__(self, pin):
        pin.onto(self._protocol)

    def __getddpin__(self):
        return Pin.get_from(self._protocol)


@with_traced_module
async def _traced_connect(asyncpg, pin, func, instance, args, kwargs):
    """Traced asyncpg.connect().

    connect() is instrumented and patched to return a connection proxy.
    """
    with pin.tracer.trace(
        "postgres.connect", span_type=SpanTypes.SQL, service=ext_service(pin, config.asyncpg)
    ) as span:
        # Need an ObjectProxy since Connection uses slots
        conn = _TracedConnection(await func(*args, **kwargs), pin)
        span.set_tags(_get_connection_tags(conn))
        return conn


async def _traced_query(pin, method, query, args, kwargs):
    with pin.tracer.trace(
        "postgres.query", resource=query, service=ext_service(pin, config.asyncpg), span_type=SpanTypes.SQL
    ) as span:
        span.set_tag(SPAN_MEASURED_KEY)
        span.set_tags(pin.tags)
        return await method(*args, **kwargs)


@with_traced_module
async def _traced_protocol_execute(asyncpg, pin, func, instance, args, kwargs):
    state = get_argument_value(args, kwargs, 0, "state")  # type: Union[str, PreparedStatement]
    query = state if isinstance(state, str) else state.query
    return await _traced_query(pin, func, query, args, kwargs)


def _patch(asyncpg):
    # type: (ModuleType) -> None
    wrap(asyncpg, "connect", _traced_connect(asyncpg))
    for method in ("execute", "bind_execute", "query", "bind_execute_many"):
        wrap(asyncpg.protocol, "Protocol.%s" % method, _traced_protocol_execute(asyncpg))


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
    for method in ("execute", "bind_execute", "query", "bind_execute_many"):
        unwrap(asyncpg.protocol.Protocol, method)


def unpatch():
    # type: () -> None
    import asyncpg

    if not getattr(asyncpg, "_datadog_patch", False):
        return

    _unpatch(asyncpg)

    setattr(asyncpg, "_datadog_patch", False)
