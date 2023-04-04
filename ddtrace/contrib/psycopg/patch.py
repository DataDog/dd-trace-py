import os

import psycopg
from psycopg.sql import Composable
from psycopg.sql import SQL

from ddtrace import Pin
from ddtrace import config
from ddtrace.vendor import wrapt

from ...internal.utils.formats import asbool
from ...internal.utils.version import parse_version
from ...propagation._database_monitoring import _DBM_Propagator
from ..utils import PsycopgTracedConnection
from ..utils import PsycopgTracedCursor
from ..utils import patched_connect
from ..utils import psycopg_sql_injector_factory
from ..utils_async import PsycopgTracedAsyncConnection
from ..utils_async import PsycopgTracedAsyncCursor
from ..utils_async import patched_connect_async


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

    wrapt.wrap_function_wrapper(psycopg, "connect", patched_connect_psycopg3)
    wrapt.wrap_function_wrapper(psycopg.Connection, "connect", patched_connect_psycopg3)
    wrapt.wrap_function_wrapper(psycopg, "Cursor", PsycopgTracedCursor._init_from_connection)

    wrapt.wrap_function_wrapper(psycopg.AsyncConnection, "connect", patched_connect_async_psycopg3)
    wrapt.wrap_function_wrapper(psycopg, "AsyncCursor", PsycopgTracedAsyncCursor._init_from_connection)


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


class Psycopg3TracedConnection(PsycopgTracedConnection):
    def __init__(self, *args, **kwargs):
        PsycopgTracedConnection.__init__(self, conn=args[0])

    def execute(self, *args, **kwargs):
        """Execute a query and return a cursor to read its results."""
        span_name = "{}.{}".format(self._self_datadog_name, "execute")

        def patched_execute(*args, **kwargs):
            try:
                cur = self.cursor()
                if kwargs.get("binary", None):
                    cur.format = 1  # set to 1 for binary or 0 if not
                return cur.execute(*args, **kwargs)
            except Exception as ex:
                raise ex.with_traceback(None)

        return self._trace_method(patched_execute, span_name, {}, *args, **kwargs)


class Psycopg3TracedAsyncConnection(PsycopgTracedAsyncConnection):
    def __init__(self, *args, **kwargs):
        super(Psycopg3TracedAsyncConnection, self).__init__(*args, **kwargs)

    async def execute(self, *args, **kwargs):  # noqa
        """Execute a query and return a cursor to read its results."""
        span_name = "{}.{}".format(self._self_datadog_name, "execute")

        async def patched_execute(*args, **kwargs):
            try:
                cur = await self.cursor()
                if kwargs.get("binary", None):
                    cur.format = 1  # set to 1 for binary or 0 if not
                return cur.execute(*args, **kwargs)
            except Exception as ex:
                raise ex.with_traceback(None)

        return await self._trace_method(patched_execute, span_name, {}, *args, **kwargs)


def patched_connect_psycopg3(connect_func, _, args, kwargs):
    kwargs["traced_conn_cls"] = Psycopg3TracedConnection
    return patched_connect(connect_func, _, args, kwargs)


def patched_connect_async_psycopg3(connect_func, _, args, kwargs):
    kwargs["traced_conn_cls"] = Psycopg3TracedAsyncConnection
    return patched_connect_async(connect_func, _, args, kwargs)
