from __future__ import annotations

from types import ModuleType
from typing import TYPE_CHECKING
from typing import Any
from typing import Callable
from typing import Union

import asyncpg
import wrapt

from ddtrace import config
from ddtrace._trace.pin import Pin
from ddtrace.contrib._events.dbapi import DatabaseDbmInjectionEvent
from ddtrace.contrib._events.dbapi import DatabaseOperationEvent
from ddtrace.contrib.internal.trace_utils import ext_service
from ddtrace.contrib.internal.trace_utils import iswrapped
from ddtrace.contrib.internal.trace_utils import unwrap
from ddtrace.contrib.internal.trace_utils import wrap
from ddtrace.contrib.internal.trace_utils_async import with_traced_module
from ddtrace.ext import db
from ddtrace.ext import net
from ddtrace.internal import core
from ddtrace.internal.logger import get_logger
from ddtrace.internal.schema import schematize_database_operation
from ddtrace.internal.schema import schematize_service_name
from ddtrace.internal.utils import get_argument_value
from ddtrace.propagation._database_monitoring import _DBM_Propagator


if TYPE_CHECKING:  # pragma: no cover
    from asyncpg.prepared_stmt import PreparedStatement


DBMS_NAME = "postgresql"

_PROTOCOL_METHODS = ("execute", "bind_execute", "query", "bind_execute_many")


config._add(
    "asyncpg",
    dict(
        _default_service=schematize_service_name("postgres"),
        _dbm_propagator=_DBM_Propagator(0, "query"),
    ),
)


log = get_logger(__name__)


def get_version() -> str:
    return getattr(asyncpg, "__version__", "")


def _supported_versions() -> dict[str, str]:
    return {"asyncpg": ">=0.23.0"}


def _get_connection_tags(conn: asyncpg.Connection) -> dict[str, str]:
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
        super().__init__(conn)
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
async def _traced_connect(
    asyncpg: ModuleType,
    pin: Pin,
    func: Callable[..., Any],
    instance: Any,
    args: tuple[Any, ...],
    kwargs: dict[str, Any],
) -> Any:
    """Traced asyncpg.connect().

    connect() is instrumented and patched to return a connection proxy.
    """
    # When using a pool, there's a connection_class args
    is_pool_context = "connection_class" in kwargs

    event = DatabaseOperationEvent(
        component=config.asyncpg.integration_name,
        integration_config=config.asyncpg,
        operation_name="postgres.connect",
        service=ext_service(pin, config.asyncpg),
        service_from_config=True,
        metadata_before_tags=True,
        measured=False,
        db_system=DBMS_NAME,
    )
    with core.context_with_event(event):
        raw_conn = await func(*args, **kwargs)
        if is_pool_context:
            # Return the unwrapped connection to avoid _TracedConnection errors
            # when using a pool with a custom connect param
            connection_tags = _get_connection_tags(raw_conn)
            connection_tags[db.SYSTEM] = DBMS_NAME
            conn_pin = pin.clone(tags=connection_tags)
            conn_pin.onto(raw_conn._protocol)
            event.set_connection_tags(connection_tags)
            # Returns a asyncpg.connection.Connection object
            return raw_conn
        else:
            # # Need an ObjectProxy when not using pools since Connection uses slots
            conn = _TracedConnection(raw_conn, pin)
            event.set_connection_tags(_get_connection_tags(conn))
            # Returns a _TracedConnection object
            return conn


async def _traced_query(
    pin: Pin, method: Callable[..., Any], query: Any, args: tuple[Any, ...], kwargs: dict[str, Any]
) -> Any:
    event = DatabaseOperationEvent(
        component=config.asyncpg.integration_name,
        integration_config=config.asyncpg,
        operation_name=schematize_database_operation("postgres.query", database_provider="postgresql"),
        service=ext_service(pin, config.asyncpg),
        service_from_config=True,
        metadata_before_tags=True,
        resource=query,
        db_system=DBMS_NAME,
        connection_tags=pin.tags,
    )
    with core.context_with_event(event) as ctx:
        # dispatch DBM
        # bind_execute_many uses prepared statements which we want to avoid injection for.
        injection = DatabaseDbmInjectionEvent(
            context=ctx, args=args, kwargs=kwargs, supports_query_rewriting=method.__name__ != "bind_execute_many"
        )
        core.dispatch_event(injection)
        args, kwargs = injection.args, injection.kwargs

        return await method(*args, **kwargs)


@with_traced_module
async def _traced_protocol_execute(asyncpg, pin, func, instance, args, kwargs):
    state: Union[str, PreparedStatement] = get_argument_value(args, kwargs, 0, "state")
    query = state if isinstance(state, str) or isinstance(state, bytes) else state.query
    return await _traced_query(pin, func, query, args, kwargs)


def _patch(asyncpg: ModuleType) -> None:
    wrap(asyncpg, "connect", _traced_connect(asyncpg))
    for method in _PROTOCOL_METHODS:
        wrap(asyncpg.protocol, "Protocol.%s" % method, _traced_protocol_execute(asyncpg))


def patch() -> None:
    import asyncpg

    if getattr(asyncpg, "_datadog_patch", False):
        return

    Pin().onto(asyncpg)
    _patch(asyncpg)

    asyncpg._datadog_patch = True


def _unpatch(asyncpg: ModuleType) -> None:
    if iswrapped(asyncpg, "connect"):
        unwrap(asyncpg, "connect")
    for method in _PROTOCOL_METHODS:
        # DEV: Use vars(cls) + delattr rather than iswrapped(cls.attr) + unwrap.
        # iswrapped/getattr access reads the descriptor; under wrapt 2.2.0,
        # FunctionWrapper.__get__ calls the inner Cython method_descriptor's tp_descr_get
        # with Py_None instead of NULL, raising:
        #   TypeError: descriptor 'execute' for 'asyncpg.protocol.protocol.BaseProtocol'
        #              objects doesn't apply to a 'NoneType' object
        # vars()/__dict__ lookup and delattr bypass the descriptor entirely.
        # Using delattr (rather than setattr to the original) also avoids leaving a Cython
        # descriptor shadow in Protocol.__dict__ that a later patch() would re-wrap instead
        # of resolving the method naturally via MRO from BaseProtocol.
        if method in asyncpg.protocol.Protocol.__dict__:
            delattr(asyncpg.protocol.Protocol, method)


def unpatch() -> None:
    import asyncpg

    if not getattr(asyncpg, "_datadog_patch", False):
        return

    _unpatch(asyncpg)

    asyncpg._datadog_patch = False
