import importlib
import os
from typing import Any
from typing import Optional

import mysql.connector
import wrapt

from ddtrace import config
from ddtrace._trace.pin import Pin
from ddtrace.contrib.dbapi import TracedConnection
from ddtrace.contrib.dbapi_async import TracedAsyncConnection
from ddtrace.contrib.internal.trace_utils import _convert_to_string
from ddtrace.ext import db
from ddtrace.ext import net
from ddtrace.internal.compat import is_wrapted
from ddtrace.internal.schema import schematize_database_operation
from ddtrace.internal.schema import schematize_service_name
from ddtrace.internal.settings.asm import config as asm_config
from ddtrace.internal.utils.formats import asbool
from ddtrace.propagation._database_monitoring import _DBM_Propagator


config._add(
    "mysql",
    dict(
        _default_service=schematize_service_name("mysql"),
        _dbapi_span_name_prefix="mysql",
        _dbapi_span_operation_name=schematize_database_operation("mysql.query", database_provider="mysql"),
        trace_fetch_methods=asbool(os.getenv("DD_MYSQL_TRACE_FETCH_METHODS", default=False)),
        _dbm_propagator=_DBM_Propagator(0, "query"),
    ),
)


def get_version() -> str:
    return mysql.connector.version.VERSION_TEXT


def _supported_versions() -> dict[str, str]:
    return {"mysql": ">=8.0.5"}


CONN_ATTR_BY_TAG = {
    net.TARGET_HOST: "server_host",
    net.TARGET_PORT: "server_port",
    net.SERVER_ADDRESS: "server_host",
    db.USER: "user",
    db.NAME: "database",
}


def _get_conn_tags(conn) -> dict[str, str]:
    tags = {}
    for tag, attr in CONN_ATTR_BY_TAG.items():
        value = getattr(conn, attr, "")
        if value == "":
            continue
        string_value = _convert_to_string(value)
        if string_value is not None:
            tags[tag] = string_value
    return tags


def _get_mysql_aio_module() -> Optional[Any]:
    aio_module = getattr(mysql.connector, "aio", None)
    if aio_module is not None:
        return aio_module

    try:
        return importlib.import_module("mysql.connector.aio")
    except ImportError:
        return None


def patch():
    wrapt.wrap_function_wrapper("mysql.connector", "connect", _connect)
    aio_module = _get_mysql_aio_module()
    if aio_module is not None and hasattr(aio_module, "connect"):
        wrapt.wrap_function_wrapper("mysql.connector.aio", "connect", _connect_async)

    # `Connect` is an alias for `connect`, patch it too
    if hasattr(mysql.connector, "Connect"):
        mysql.connector.Connect = mysql.connector.connect

    if asm_config._iast_enabled:
        from ddtrace.appsec._iast._metrics import _set_metric_iast_instrumented_sink
        from ddtrace.appsec._iast.constants import VULN_SQL_INJECTION

        _set_metric_iast_instrumented_sink(VULN_SQL_INJECTION)
    mysql.connector._datadog_patch = True


def unpatch():
    if is_wrapted(mysql.connector.connect):
        mysql.connector.connect = mysql.connector.connect.__wrapped__
        if hasattr(mysql.connector, "Connect"):
            mysql.connector.Connect = mysql.connector.connect

    aio_module = _get_mysql_aio_module()
    if aio_module is not None and hasattr(aio_module, "connect") and is_wrapted(aio_module.connect):
        aio_module.connect = aio_module.connect.__wrapped__

    mysql.connector._datadog_patch = False


def _connect(func, instance, args, kwargs):
    conn = func(*args, **kwargs)
    return patch_conn(conn)


async def _connect_async(func, instance, args, kwargs):
    conn = await func(*args, **kwargs)
    return patch_conn_async(conn)


def patch_conn(conn):
    tags = _get_conn_tags(conn)
    tags[db.SYSTEM] = "mysql"
    pin = Pin(tags=tags)

    # grab the metadata from the conn
    wrapped = TracedConnection(conn, pin=pin, cfg=config.mysql)
    pin.onto(wrapped)
    return wrapped


def patch_conn_async(conn):
    tags = _get_conn_tags(conn)
    tags[db.SYSTEM] = "mysql"
    pin = Pin(tags=tags)

    wrapped = TracedAsyncConnection(conn, pin=pin, cfg=config.mysql)
    pin.onto(wrapped)
    return wrapped
