import mysql.connector
from wrapt import wrap_function_wrapper as _w

from ddtrace import config
from ddtrace.contrib.dbapi import TracedConnection
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
    db.NAME: "database",
}


def _connect(func, instance, args, kwargs):
    conn = func(*args, **kwargs)
    return patch_conn(conn)


def patch_conn(conn):
    tags = {
        t: _convert_to_string(getattr(conn, a, None)) for t, a in CONN_ATTR_BY_TAG.items() if getattr(conn, a, "") != ""
    }
    tags[db.SYSTEM] = "mysql"
    return TracedConnection(conn, cfg=config.mysql, db_tags=tags)


def patch():
    if getattr(mysql, "_datadog_patch", False):
        return

    mysql._datadog_patch = True
    _w("mysql.connector", "connect", _connect)

    if asm_config._iast_enabled:
        from ddtrace.appsec._iast._metrics import _set_metric_iast_instrumented_sink
        from ddtrace.appsec._iast.constants import VULN_SQL_INJECTION

        _set_metric_iast_instrumented_sink(VULN_SQL_INJECTION)


def unpatch():
    if not getattr(mysql, "_datadog_patch", False):
        return

    mysql._datadog_patch = False
    _u(mysql.connector, "connect")
