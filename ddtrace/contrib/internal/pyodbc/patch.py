import pyodbc

from ddtrace import config
from ddtrace.contrib.dbapi import TracedConnection
from ddtrace.contrib.dbapi import TracedCursor
from ddtrace.contrib.internal.trace_utils import unwrap
from ddtrace.contrib.internal.trace_utils import wrap
from ddtrace.ext import db
from ddtrace.internal.schema import schematize_service_name
from ddtrace.internal.settings import env
from ddtrace.internal.utils.formats import asbool


config._add(
    "pyodbc",
    dict(
        _default_service=schematize_service_name("pyodbc"),
        _dbapi_span_name_prefix="pyodbc",
        trace_fetch_methods=asbool(env.get("DD_PYODBC_TRACE_FETCH_METHODS", default=False)),
    ),
)


def get_version() -> str:
    return pyodbc.version


def _supported_versions() -> dict[str, str]:
    return {"pyodbc": ">=4.0.31"}


def patch():
    if getattr(pyodbc, "_datadog_patch", False):
        return
    pyodbc._datadog_patch = True
    wrap("pyodbc", "connect", _connect)


def unpatch():
    if getattr(pyodbc, "_datadog_patch", False):
        pyodbc._datadog_patch = False
        unwrap(pyodbc, "connect")


def _connect(func, instance, args, kwargs):
    conn = func(*args, **kwargs)
    return patch_conn(conn)


def patch_conn(conn):
    try:
        tags = {
            db.SYSTEM: conn.getinfo(pyodbc.SQL_DBMS_NAME),
            db.USER: conn.getinfo(pyodbc.SQL_USER_NAME),
        }
    except pyodbc.Error:
        tags = {}
    return PyODBCTracedConnection(conn, db_tags=tags)


class PyODBCTracedCursor(TracedCursor):
    pass


class PyODBCTracedConnection(TracedConnection):
    def __init__(self, conn, cursor_cls=None, db_tags=None):
        if not cursor_cls:
            cursor_cls = PyODBCTracedCursor
        super(PyODBCTracedConnection, self).__init__(conn, cfg=config.pyodbc, cursor_cls=cursor_cls, db_tags=db_tags)
