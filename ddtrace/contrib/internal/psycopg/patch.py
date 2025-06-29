from importlib import import_module
import inspect
import os
from typing import Dict  # noqa:F401
from typing import List  # noqa:F401

from wrapt import wrap_function_wrapper as _w

from ddtrace import config
from ddtrace.contrib import dbapi
from ddtrace.contrib.internal.psycopg.async_connection import patched_connect_async_factory
from ddtrace.contrib.internal.psycopg.async_cursor import Psycopg3FetchTracedAsyncCursor
from ddtrace.contrib.internal.psycopg.async_cursor import Psycopg3TracedAsyncCursor
from ddtrace.contrib.internal.psycopg.connection import patched_connect_factory
from ddtrace.contrib.internal.psycopg.cursor import Psycopg3FetchTracedCursor
from ddtrace.contrib.internal.psycopg.cursor import Psycopg3TracedCursor
from ddtrace.contrib.internal.psycopg.extensions import _patch_extensions
from ddtrace.contrib.internal.psycopg.extensions import _unpatch_extensions
from ddtrace.contrib.internal.psycopg.extensions import get_psycopg2_extensions
from ddtrace.internal.schema import schematize_database_operation
from ddtrace.internal.schema import schematize_service_name
from ddtrace.internal.utils.formats import asbool
from ddtrace.internal.utils.wrappers import unwrap as _u
from ddtrace.propagation._database_monitoring import _DBM_Propagator
from ddtrace.propagation._database_monitoring import default_sql_injector as _default_sql_injector
from ddtrace.trace import Pin


try:
    psycopg_import = import_module("psycopg")

    # must get the original connect class method from the class __dict__ to use later in unpatch
    # Python 3.11 and wrapt result in the class method being rebinded as an instance method when
    # using unwrap
    _original_connect = psycopg_import.Connection.__dict__["connect"]
    _original_async_connect = psycopg_import.AsyncConnection.__dict__["connect"]
# AttributeError can happen due to circular imports under certain integration methods
except (ImportError, AttributeError):
    pass


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
        _default_service=schematize_service_name("postgres"),
        _dbapi_span_name_prefix="postgres",
        _dbapi_span_operation_name=schematize_database_operation("postgres.query", database_provider="postgresql"),
        _patched_modules=set(),
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


def get_version():
    # type: () -> str
    return ""


PATCHED_VERSIONS = {}


def _supported_versions() -> Dict[str, str]:
    return {"psycopg": ">=3.0.0", "psycopg2": ">=2.8.0"}


def get_versions():
    # type: () -> List[str]
    return PATCHED_VERSIONS


def format_version(version: str) -> str:
    return ".".join(map(lambda x: x.split(" ")[0], version.split(".")[:3]))


def _psycopg_modules():
    module_names = (
        "psycopg",
        "psycopg2",
    )
    for module_name in module_names:
        try:
            module = import_module(module_name)
            PATCHED_VERSIONS[module_name] = format_version(getattr(module, "__version__", ""))
            yield module
        except ImportError:
            pass


def patch():
    for psycopg_module in _psycopg_modules():
        _patch(psycopg_module)


def _patch(psycopg_module):
    """Patch monkey patches psycopg's connection function
    so that the connection's functions are traced.
    """
    if getattr(psycopg_module, "_datadog_patch", False):
        return
    psycopg_module._datadog_patch = True

    Pin(_config=config.psycopg).onto(psycopg_module)

    if psycopg_module.__name__ == "psycopg2":
        # patch all psycopg2 extensions
        _psycopg2_extensions = get_psycopg2_extensions(psycopg_module)
        config.psycopg["_extensions_to_patch"] = _psycopg2_extensions
        _patch_extensions(_psycopg2_extensions)

        _w(psycopg_module, "connect", patched_connect_factory(psycopg_module))

        config.psycopg["_patched_modules"].add(psycopg_module)
    else:
        _w(psycopg_module, "connect", patched_connect_factory(psycopg_module))
        _w(psycopg_module, "Cursor", init_cursor_from_connection_factory(psycopg_module))
        _w(psycopg_module, "AsyncCursor", init_cursor_from_connection_factory(psycopg_module))

        _w(psycopg_module.Connection, "connect", patched_connect_factory(psycopg_module))
        _w(psycopg_module.AsyncConnection, "connect", patched_connect_async_factory(psycopg_module))

        config.psycopg["_patched_modules"].add(psycopg_module)


def unpatch():
    for psycopg_module in _psycopg_modules():
        _unpatch(psycopg_module)


def _unpatch(psycopg_module):
    if getattr(psycopg_module, "_datadog_patch", False):
        psycopg_module._datadog_patch = False

        if psycopg_module.__name__ == "psycopg2":
            _u(psycopg_module, "connect")

            _psycopg2_extensions = get_psycopg2_extensions(psycopg_module)
            _unpatch_extensions(_psycopg2_extensions)
        else:
            _u(psycopg_module, "connect")
            _u(psycopg_module, "Cursor")
            _u(psycopg_module, "AsyncCursor")

            # _u throws an attribute error for Python 3.11, no __get__ on the BoundFunctionWrapper
            # unlike Python Class Methods which implement __get__
            psycopg_module.Connection.connect = _original_connect
            psycopg_module.AsyncConnection.connect = _original_async_connect

        pin = Pin.get_from(psycopg_module)
        if pin:
            pin.remove_from(psycopg_module)


def init_cursor_from_connection_factory(psycopg_module):
    def init_cursor_from_connection(wrapped_cursor_cls, _, args, kwargs):
        connection = kwargs.pop("connection", None)
        if not connection:
            args = list(args)
            index = next((i for i, x in enumerate(args) if isinstance(x, dbapi.TracedConnection)), None)
            if index is not None:
                connection = args.pop(index)

            # if we do not have an example of a traced connection, call the original cursor function
            if not connection:
                return wrapped_cursor_cls(*args, **kwargs)

        pin = Pin.get_from(connection).clone()
        cfg = config.psycopg

        if cfg and cfg.trace_fetch_methods:
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
            cursor = wrapped_cursor_cls(connection=connection, row_factory=row_factory, *args, **kwargs)  # noqa: B026
        else:
            cursor = wrapped_cursor_cls(connection, *args, **kwargs)

        return traced_cursor_cls(cursor=cursor, pin=pin, cfg=cfg)

    return init_cursor_from_connection
