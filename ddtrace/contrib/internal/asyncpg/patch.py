from types import ModuleType
from typing import TYPE_CHECKING  # noqa:I001
from typing import Dict

import asyncpg
import wrapt

from ddtrace import config
from ddtrace.constants import _SPAN_MEASURED_KEY
from ddtrace.constants import SPAN_KIND
from ddtrace.contrib.internal.trace_utils import ext_service
from ddtrace.contrib.internal.trace_utils import unwrap
from ddtrace.contrib.internal.trace_utils import wrap
from ddtrace.contrib.internal.trace_utils_async import with_traced_module
from ddtrace.ext import SpanKind
from ddtrace.ext import SpanTypes
from ddtrace.ext import db
from ddtrace.ext import net
from ddtrace.internal import core
from ddtrace.internal.constants import COMPONENT
from ddtrace.internal.logger import get_logger
from ddtrace.internal.schema import schematize_database_operation
from ddtrace.internal.schema import schematize_service_name
from ddtrace.internal.utils import get_argument_value
from ddtrace.propagation._database_monitoring import _DBM_Propagator
from ddtrace.trace import Pin


if TYPE_CHECKING:  # pragma: no cover
    from typing import Union  # noqa:F401

    from asyncpg.prepared_stmt import PreparedStatement  # noqa:F401


DBMS_NAME = "postgresql"


config._add(
    "asyncpg",
    dict(
        _default_service=schematize_service_name("postgres"),
        _dbm_propagator=_DBM_Propagator(0, "query"),
    ),
)


log = get_logger(__name__)


def get_version():
    # type: () -> str
    return getattr(asyncpg, "__version__", "")


def _supported_versions() -> Dict[str, str]:
    return {"asyncpg": ">=0.22.0"}


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
        net.SERVER_ADDRESS: host,
        db.USER: params.user,
        db.NAME: params.database,
    }


class _TracedConnection(wrapt.ObjectProxy):
    def __init__(self, conn, pin):
        super(_TracedConnection, self).__init__(conn)
        tags = _get_connection_tags(conn)
        tags[db.SYSTEM] = DBMS_NAME
        conn_pin = pin.clone(tags=tags)
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
        span.set_tag_str(COMPONENT, config.asyncpg.integration_name)
        span.set_tag_str(db.SYSTEM, DBMS_NAME)

        # set span.kind to the type of request being performed
        span.set_tag_str(SPAN_KIND, SpanKind.CLIENT)

        # Need an ObjectProxy since Connection uses slots
        conn = _TracedConnection(await func(*args, **kwargs), pin)
        span.set_tags(_get_connection_tags(conn))
        return conn


async def _traced_query(pin, method, query, args, kwargs):
    with pin.tracer.trace(
        schematize_database_operation("postgres.query", database_provider="postgresql"),
        resource=query,
        service=ext_service(pin, config.asyncpg),
        span_type=SpanTypes.SQL,
    ) as span:
        span.set_tag_str(COMPONENT, config.asyncpg.integration_name)
        span.set_tag_str(db.SYSTEM, DBMS_NAME)

        # set span.kind to the type of request being performed
        span.set_tag_str(SPAN_KIND, SpanKind.CLIENT)
        span.set_tag(_SPAN_MEASURED_KEY)
        span.set_tags(pin.tags)

        # dispatch DBM
        result = core.dispatch_with_results("asyncpg.execute", (config.asyncpg, method, span, args, kwargs)).result
        if result:
            span, args, kwargs = result.value

        return await method(*args, **kwargs)


@with_traced_module
async def _traced_protocol_execute(asyncpg, pin, func, instance, args, kwargs):
    state = get_argument_value(args, kwargs, 0, "state")  # type: Union[str, PreparedStatement]
    query = state if isinstance(state, str) or isinstance(state, bytes) else state.query
    return await _traced_query(pin, func, query, args, kwargs)


def _patch(asyncpg: ModuleType) -> None:
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

    asyncpg._datadog_patch = True


def _unpatch(asyncpg: ModuleType) -> None:
    unwrap(asyncpg, "connect")
    for method in ("execute", "bind_execute", "query", "bind_execute_many"):
        unwrap(asyncpg.protocol.Protocol, method)


def unpatch():
    # type: () -> None
    import asyncpg

    if not getattr(asyncpg, "_datadog_patch", False):
        return

    _unpatch(asyncpg)

    asyncpg._datadog_patch = False
