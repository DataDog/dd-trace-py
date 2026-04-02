import mysql.connector
from wrapt import wrap_function_wrapper as _w

from ddtrace import config
from ddtrace.contrib.dbapi import TracedConnection
from ddtrace.contrib.dbapi_async import TracedAsyncConnection
from ddtrace.contrib.internal.trace_utils import _convert_to_string
from ddtrace.ext import db
from ddtrace.ext import net
from ddtrace.internal.schema import schematize_database_operation
from ddtrace.internal.schema import schematize_service_name
from ddtrace.internal.settings import env
from ddtrace.internal.settings.asm import config as asm_config
from ddtrace.internal.utils.formats import asbool
from ddtrace.internal.utils.wrappers import unwrap as _u
from ddtrace.propagation._database_monitoring import _DBM_Propagator


config._add(
    "mysql",
    dict(
        _default_service=schematize_service_name("mysql"),
        _dbapi_span_name_prefix="mysql",
        _dbapi_span_operation_name=schematize_database_operation("mysql.query", database_provider="mysql"),
        trace_fetch_methods=asbool(env.get("DD_MYSQL_TRACE_FETCH_METHODS", default=False)),
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
}


def _build_common_conn_tags(conn):
    tags = {}
    for tag_name, attr_name in CONN_ATTR_BY_TAG.items():
        if value := getattr(conn, attr_name, None):
            value = _convert_to_string(value)
            if value is not None:
                tags[tag_name] = value
    return tags


def patch_conn(conn, traced_conn_cls=TracedConnection):
    tags = _build_common_conn_tags(conn)
    # for aio.connector, we cannot access directly database so we have
    # to treat it differently
    if database := _convert_to_string(getattr(conn, "database", None)):
        tags[db.NAME] = database
    tags[db.SYSTEM] = "mysql"
    return traced_conn_cls(conn, cfg=config.mysql, db_tags=tags)


def _connect(func, instance, args, kwargs):
    conn = func(*args, **kwargs)
    return patch_conn(conn)


async def _connect_async(func, instance, args, kwargs):
    conn = await func(*args, **kwargs)
    tags = _build_common_conn_tags(conn)

    try:
        database = await conn.get_database()
        tags[db.NAME] = database
    except Exception:
        database = None

    tags[db.SYSTEM] = "mysql"
    return TracedAsyncConnection(conn, cfg=config.mysql, db_tags=tags)


def patch():
    if getattr(mysql, "_datadog_patch", False):
        return

    mysql._datadog_patch = True
    _w("mysql.connector", "connect", _connect)

    if getattr(mysql.connector, "aio", None):
        _w("mysql.connector.aio", "connect", _connect_async)

    if asm_config._iast_enabled:
        from ddtrace.appsec._iast._metrics import _set_metric_iast_instrumented_sink
        from ddtrace.appsec._iast.constants import VULN_SQL_INJECTION

        _set_metric_iast_instrumented_sink(VULN_SQL_INJECTION)


def unpatch():
    if not getattr(mysql, "_datadog_patch", False):
        return

    mysql._datadog_patch = False
    _u(mysql.connector, "connect")

    if getattr(mysql.connector, "aio", None):
        _u(mysql.connector.aio, "connect")
