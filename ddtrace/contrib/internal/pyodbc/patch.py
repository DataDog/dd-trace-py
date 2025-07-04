import os
from typing import Dict

import pyodbc

from ddtrace import config
from ddtrace.contrib.dbapi import TracedConnection
from ddtrace.contrib.dbapi import TracedCursor
from ddtrace.contrib.internal.trace_utils import unwrap
from ddtrace.contrib.internal.trace_utils import wrap
from ddtrace.ext import db
from ddtrace.internal.schema import schematize_service_name
from ddtrace.internal.utils.formats import asbool
from ddtrace.trace import Pin


config._add(
    "pyodbc",
    dict(
        _default_service=schematize_service_name("pyodbc"),
        _dbapi_span_name_prefix="pyodbc",
        trace_fetch_methods=asbool(os.getenv("DD_PYODBC_TRACE_FETCH_METHODS", default=False)),
    ),
)


def get_version():
    # type: () -> str
    return pyodbc.version


def _supported_versions() -> Dict[str, str]:
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
    pin = Pin(service=None, tags=tags)
    wrapped = PyODBCTracedConnection(conn, pin=pin)
    pin.onto(wrapped)
    return wrapped


class PyODBCTracedCursor(TracedCursor):
    pass


class PyODBCTracedConnection(TracedConnection):
    def __init__(self, conn, pin=None, cursor_cls=None):
        if not cursor_cls:
            cursor_cls = PyODBCTracedCursor
        super(PyODBCTracedConnection, self).__init__(conn, pin, config.pyodbc, cursor_cls=cursor_cls)
