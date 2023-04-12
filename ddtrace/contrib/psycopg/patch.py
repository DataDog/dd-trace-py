import inspect
import os

import psycopg
from psycopg.sql import Composable
from psycopg.sql import SQL

from ddtrace import Pin
from ddtrace import config
from ddtrace.contrib import dbapi
from ddtrace.contrib.psycopg.async_connection import patched_connect_async
from ddtrace.contrib.psycopg.async_cursor import Psycopg3FetchTracedAsyncCursor
from ddtrace.contrib.psycopg.async_cursor import Psycopg3TracedAsyncCursor
from ddtrace.contrib.psycopg.connection import patched_connect
from ddtrace.contrib.psycopg.cursor import Psycopg3FetchTracedCursor
from ddtrace.contrib.psycopg.cursor import Psycopg3TracedCursor
from ddtrace.contrib.psycopg.utils import psycopg_sql_injector_factory
from ddtrace.vendor import wrapt

from ...internal.utils.formats import asbool
from ...internal.utils.version import parse_version
from ...propagation._database_monitoring import _DBM_Propagator


config._add(
    "psycopg",
    dict(
        _default_service="postgres",
        _dbapi_span_name_prefix="postgres",
        trace_fetch_methods=asbool(os.getenv("DD_PSYCOPG_TRACE_FETCH_METHODS", default=False)),
        trace_connect=asbool(os.getenv("DD_PSYCOPG_TRACE_CONNECT", default=False)),
        _dbm_propagator=_DBM_Propagator(
            0, "query", psycopg_sql_injector_factory(composable_class=Composable, sql_class=SQL)
        ),
        dbms_name="postgresql",
    ),
)

# Original methods
_connect = psycopg.connect
_connection_connect = psycopg.Connection.connect
_cursor_init = psycopg.Cursor
_async_connection_connect = psycopg.AsyncConnection.connect
_async_cursor_init = psycopg.AsyncCursor


PSYCOPG_VERSION = parse_version(psycopg.__version__)


def patch():
    """Patch monkey patches psycopg's connection function
    so that the connection's functions are traced.
    """
    if getattr(psycopg, "_datadog_patch", False):
        return
    setattr(psycopg, "_datadog_patch", True)

    Pin(_config=config.psycopg).onto(psycopg)
    config.psycopg.base_module = psycopg

    wrapt.wrap_function_wrapper(psycopg, "connect", patched_connect)
    wrapt.wrap_function_wrapper(psycopg.Connection, "connect", patched_connect)
    wrapt.wrap_function_wrapper(psycopg, "Cursor", init_cursor_from_connection)

    wrapt.wrap_function_wrapper(psycopg.AsyncConnection, "connect", patched_connect_async)
    wrapt.wrap_function_wrapper(psycopg, "AsyncCursor", init_cursor_from_connection)


def unpatch():
    if getattr(psycopg, "_datadog_patch", False):
        setattr(psycopg, "_datadog_patch", False)
        psycopg.connect = _connect
        psycopg.Connection.connect = _connection_connect
        psycopg.Cursor = _cursor_init
        psycopg.AsyncConnection.connect = _async_connection_connect
        psycopg.AsyncCursor = _async_cursor_init

        pin = Pin.get_from(psycopg)
        if pin:
            pin.remove_from(psycopg)


def init_cursor_from_connection(wrapped_cursor_cls, _, args, kwargs):
    connection = kwargs.pop("connection", None)
    if not connection:
        args = list(args)
        connection = args.pop(next((i for i, x in enumerate(args) if isinstance(x, dbapi.TracedConnection)), None))
    pin = Pin.get_from(connection).clone()
    cfg = globals()["config"]._config["psycopg"]

    if cfg and getattr(cfg, "trace_fetch_methods") and cfg.trace_fetch_methods:
        trace_fetch_methods = True
    else:
        trace_fetch_methods = False

    if issubclass(wrapped_cursor_cls, psycopg.AsyncCursor):
        traced_cursor_cls = Psycopg3FetchTracedAsyncCursor if trace_fetch_methods else Psycopg3TracedAsyncCursor
    else:
        traced_cursor_cls = Psycopg3FetchTracedCursor if trace_fetch_methods else Psycopg3TracedCursor

    args_mapping = inspect.signature(wrapped_cursor_cls.__init__).parameters
    # inspect.signature returns ordered dict[argument_name: str, parameter_type: type]
    if "row_factory" in args_mapping and "row_factory" not in kwargs:
        # check for row_factory in args by checking for functions
        row_factory = None
        for i in range(len(args)):
            if callable(args[i]):
                row_factory = args.pop(i)
                break
        # else just use the connection row factory
        if row_factory is None:
            row_factory = connection.row_factory
        cursor = wrapped_cursor_cls(connection=connection, row_factory=row_factory, *args, **kwargs)
    else:
        cursor = wrapped_cursor_cls(connection, *args, **kwargs)

    return traced_cursor_cls(cursor=cursor, pin=pin, cfg=cfg)
