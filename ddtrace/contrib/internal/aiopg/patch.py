from typing import Any
from typing import Callable

import aiopg.connection
import psycopg2.extensions
import wrapt

from ddtrace import config
from ddtrace.contrib.internal.aiopg.connection import AIOTracedConnection
from ddtrace.contrib.internal.aiopg.connection import _wrap_context_manager
from ddtrace.contrib.internal.psycopg.connection import patch_conn as psycopg_patch_conn
from ddtrace.contrib.internal.psycopg.extensions import _patch_extensions
from ddtrace.contrib.internal.psycopg.extensions import _unpatch_extensions
from ddtrace.internal.compat import is_wrapted
from ddtrace.internal.schema import schematize_service_name
from ddtrace.internal.utils.wrappers import unwrap as _u


config._add(
    "aiopg",
    dict(
        _default_service=schematize_service_name("postgres"),
    ),
)


def get_version() -> str:
    return getattr(aiopg, "__version__", "")


def _supported_versions() -> dict[str, str]:
    return {"aiopg": ">=0.16.0"}


def patch() -> None:
    """Trace aiopg connections while preserving their context managers."""
    if getattr(aiopg, "_datadog_patch", False):
        return
    aiopg._datadog_patch = True

    for module in (aiopg, aiopg.connection, aiopg.pool):
        wrapt.wrap_function_wrapper(module, "connect", patched_connect)
    _patch_extensions(_aiopg_extensions)  # do this early just in case


def unpatch() -> None:
    if getattr(aiopg, "_datadog_patch", False):
        aiopg._datadog_patch = False
        for module in (aiopg, aiopg.connection, aiopg.pool):
            _u(module, "connect")
        _unpatch_extensions(_aiopg_extensions)


def patched_connect(connect_func: Callable[..., Any], _: Any, args: tuple[Any, ...], kwargs: dict[str, Any]) -> Any:
    return _wrap_context_manager(
        connect_func(*args, **kwargs), lambda conn: psycopg_patch_conn(conn, traced_conn_cls=AIOTracedConnection)
    )


def _extensions_register_type(func, _, args, kwargs):
    def _unroll_args(obj, scope=None):
        return obj, scope

    obj, scope = _unroll_args(*args, **kwargs)

    # register_type performs a c-level check of the object
    # type so we must be sure to pass in the actual db connection
    if scope and is_wrapted(scope):
        scope = scope.__wrapped__._conn

    return func(obj, scope) if scope else func(obj)


# extension hooks
_aiopg_extensions = [
    (psycopg2.extensions.register_type, psycopg2.extensions, "register_type", _extensions_register_type),
]
