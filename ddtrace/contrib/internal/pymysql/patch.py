import pymysql
import wrapt

from ddtrace import config
from ddtrace.contrib.dbapi import TracedConnection
from ddtrace.contrib.internal.pymysql.cursor import PyMySQLFetchTracedCursor
from ddtrace.contrib.internal.pymysql.cursor import PyMySQLTracedCursor
from ddtrace.contrib.internal.trace_utils import _convert_to_string
from ddtrace.ext import db
from ddtrace.ext import net
from ddtrace.internal import integration_hooks
from ddtrace.internal.compat import is_wrapted
from ddtrace.internal.database import DatabaseQueryAdapter
from ddtrace.internal.database import configure_database
from ddtrace.internal.schema import schematize_database_operation
from ddtrace.internal.schema import schematize_service_name
from ddtrace.internal.settings import env
from ddtrace.internal.utils.formats import asbool


config._add(
    "pymysql",
    dict(
        _default_service=schematize_service_name("pymysql"),
        _dbapi_span_name_prefix="pymysql",
        _dbapi_span_operation_name=schematize_database_operation("pymysql.query", database_provider="mysql"),
        trace_fetch_methods=asbool(env.get("DD_PYMYSQL_TRACE_FETCH_METHODS", default=False)),
        _query_adapter=DatabaseQueryAdapter(0, "query"),
    ),
)


def get_version() -> str:
    return getattr(pymysql, "__version__", "")


CONN_ATTR_BY_TAG = {
    net.TARGET_HOST: "host",
    net.TARGET_PORT: "port",
    net.SERVER_ADDRESS: "host",
    db.USER: "user",
    db.NAME: "db",
}


def _supported_versions() -> dict[str, str]:
    return {"pymysql": ">=0.10"}


def patch():
    if getattr(pymysql, "_datadog_patch", False):
        return
    wrapt.wrap_function_wrapper("pymysql", "connect", _connect)
    pymysql._datadog_patch = True
    integration_hooks.on_patch("pymysql")
    configure_database(config.pymysql)


def unpatch():
    if is_wrapted(pymysql.connect):
        pymysql.connect = pymysql.connect.__wrapped__
    pymysql._datadog_patch = False
    integration_hooks.on_unpatch("pymysql")


def _connect(func, instance, args, kwargs):
    conn = func(*args, **kwargs)
    return patch_conn(conn)


def patch_conn(conn):
    tags = {t: _convert_to_string(getattr(conn, a)) for t, a in CONN_ATTR_BY_TAG.items() if getattr(conn, a, "") != ""}
    tags[db.SYSTEM] = "mysql"
    cursor_cls = PyMySQLFetchTracedCursor if config.pymysql.trace_fetch_methods else PyMySQLTracedCursor
    return TracedConnection(conn, cfg=config.pymysql, cursor_cls=cursor_cls, db_tags=tags)
