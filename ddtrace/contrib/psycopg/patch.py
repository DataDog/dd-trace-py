from importlib import import_module
import inspect
import os

from ddtrace import Pin
from ddtrace import config
from ddtrace.contrib import dbapi


try:
    from ddtrace.contrib.psycopg.async_connection import patched_connect_async
    from ddtrace.contrib.psycopg.async_cursor import Psycopg3FetchTracedAsyncCursor
    from ddtrace.contrib.psycopg.async_cursor import Psycopg3TracedAsyncCursor
# catch async function syntax errors when using Python<3.7 with no async support
except SyntaxError:
    pass
from ddtrace.contrib.psycopg.connection import patched_connect_factory
from ddtrace.contrib.psycopg.cursor import Psycopg3FetchTracedCursor
from ddtrace.contrib.psycopg.cursor import Psycopg3TracedCursor
from ddtrace.contrib.psycopg.extensions import _patch_extensions
from ddtrace.contrib.psycopg.extensions import _unpatch_extensions
from ddtrace.contrib.psycopg.extensions import get_psycopg2_extensions
from ddtrace.propagation._database_monitoring import default_sql_injector as _default_sql_injector
from ddtrace.vendor.wrapt import wrap_function_wrapper as _w

from ...internal.utils.formats import asbool
from ...internal.utils.wrappers import unwrap as _u
from ...propagation._database_monitoring import _DBM_Propagator


def _psycopg_sql_injector(dbm_comment, sql_statement):
    for psycopg_module in config.psycopg["_patched_modules"]:
        if (
            hasattr(psycopg_module, "sql")
            and hasattr(psycopg_module.sql, "Composable")
            and isinstance(sql_statement, psycopg_module.sql.Composable)
        ):
            return psycopg_module.sql.SQL(dbm_comment) + sql_statement
    return _default_sql_injector(dbm_comment, sql_statement)


config._add(
    "psycopg",
    dict(
        _default_service="postgres",
        _dbapi_span_name_prefix="postgres",
        _patched_modules=set(),
        _patched_functions=dict(),
        trace_fetch_methods=asbool(
            os.getenv("DD_PSYCOPG_TRACE_FETCH_METHODS", default=False)
            or os.getenv("DD_PSYCOPG2_TRACE_FETCH_METHODS", default=False)
        ),
        trace_connect=asbool(
            os.getenv("DD_PSYCOPG_TRACE_CONNECT", default=False)
            or os.getenv("DD_PSYCOPG2_TRACE_CONNECT", default=False)
        ),
        _dbm_propagator=_DBM_Propagator(0, "query", _psycopg_sql_injector),
        dbms_name="postgresql",
    ),
)


def _psycopg_modules():
    module_names = (
        "psycopg",
        "psycopg2",
    )
    for module_name in module_names:
        try:
            yield import_module(module_name)
        except ImportError:
            pass


# NB: We are patching the default elasticsearch.transport module
def patch():
    for psycopg_module in _psycopg_modules():
        _patch(psycopg_module)


def _patch(psycopg_module):
    """Patch monkey patches psycopg's connection function
    so that the connection's functions are traced.
    """
    if getattr(psycopg_module, "_datadog_patch", False):
        return
    setattr(psycopg_module, "_datadog_patch", True)

    Pin(_config=config.psycopg).onto(psycopg_module)

    if psycopg_module.__name__ == "psycopg2":
        config.psycopg["_patched_functions"].update({"psycopg2.connect": psycopg_module.connect})

        # patch all psycopg2 extensions
        _psycopg2_extensions = get_psycopg2_extensions(psycopg_module)
        config.psycopg["_extensions_to_patch"] = _psycopg2_extensions
        _patch_extensions(_psycopg2_extensions)

        _w(psycopg_module, "connect", patched_connect_factory(psycopg_module))

        config.psycopg["_patched_modules"].add(psycopg_module)
    else:
        config.psycopg["_patched_functions"].update(
            {
                "psycopg.connect": psycopg_module.connect,
                "psycopg.Connection": psycopg_module.Connection,
                "psycopg.Cursor": psycopg_module.Cursor,
                "psycopg.AsyncConnection": psycopg_module.AsyncConnection,
                "psycopg.AsyncCursor": psycopg_module.AsyncCursor,
            }
        )

        _w(psycopg_module, "connect", patched_connect_factory(psycopg_module))
        _w(psycopg_module.Connection, "connect", patched_connect_factory(psycopg_module))
        _w(psycopg_module, "Cursor", init_cursor_from_connection_factory(psycopg_module))

        _w(psycopg_module.AsyncConnection, "connect", patched_connect_async)
        _w(psycopg_module, "AsyncCursor", init_cursor_from_connection_factory(psycopg_module))

        config.psycopg["_patched_modules"].add(psycopg_module)


def unpatch():
    for psycopg_module in _psycopg_modules():
        _unpatch(psycopg_module)


def _unpatch(psycopg_module):
    if getattr(psycopg_module, "_datadog_patch", False):
        setattr(psycopg_module, "_datadog_patch", False)

        if psycopg_module.__name__ == "psycopg2":
            _u(psycopg_module, "connect")

            _psycopg2_extensions = get_psycopg2_extensions(psycopg_module)
            _unpatch_extensions(_psycopg2_extensions)
        else:
            _u(psycopg_module, "connect")
            _u(psycopg_module, "Cursor")
            _u(psycopg_module, "AsyncCursor")

            try:
                _u(psycopg_module.Connection, "connect")
                _u(psycopg_module.AsyncConnection, "connect")

            # _u throws an attribute error for Python 3.11 on method objects because of
            # no __get__ method on the BoundFunctionWrapper
            except AttributeError:
                _original_connection_class = config.psycopg["_patched_functions"]["psycopg.Connection"]
                _original_asyncconnection_class = config.psycopg["_patched_functions"]["psycopg.AsyncConnection"]

                psycopg_module.Connection = _original_connection_class
                psycopg_module.AsyncConnection = _original_asyncconnection_class

        pin = Pin.get_from(psycopg_module)
        if pin:
            pin.remove_from(psycopg_module)


def init_cursor_from_connection_factory(psycopg_module):
    def init_cursor_from_connection(wrapped_cursor_cls, _, args, kwargs):
        connection = kwargs.pop("connection", None)
        if not connection:
            args = list(args)
            connection = args.pop(next((i for i, x in enumerate(args) if isinstance(x, dbapi.TracedConnection)), None))
        pin = Pin.get_from(connection).clone()
        cfg = config.psycopg

        if cfg and getattr(cfg, "trace_fetch_methods") and cfg.trace_fetch_methods:
            trace_fetch_methods = True
        else:
            trace_fetch_methods = False

        if issubclass(wrapped_cursor_cls, psycopg_module.AsyncCursor):
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

    return init_cursor_from_connection
