import os

import pyodbc

from ddtrace.internal.schema import schematize_service_name
from ddtrace.internal.utils.deprecations import DDTraceDeprecationWarning
from ddtrace.vendor.debtcollector import deprecate

from ... import Pin
from ... import config
from ...ext import db
from ...internal.utils.formats import asbool
from ..dbapi import TracedConnection
from ..dbapi import TracedCursor
from ..trace_utils import unwrap
from ..trace_utils import wrap


config._add(
    "pyodbc",
    dict(
        _default_service=schematize_service_name("pyodbc"),
        _dbapi_span_name_prefix="pyodbc",
        trace_fetch_methods=asbool(os.getenv("DD_PYODBC_TRACE_FETCH_METHODS", default=False)),
    ),
)


def _get_version():
    # type: () -> str
    return pyodbc.version


def get_version():
    deprecate(
        "get_version is deprecated",
        message="get_version is deprecated",
        removal_version="3.0.0",
        category=DDTraceDeprecationWarning,
    )
    return _get_version()


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
    return _patch_conn(conn)


def _patch_conn(conn):
    try:
        tags = {db.SYSTEM: conn.getinfo(pyodbc.SQL_DBMS_NAME), db.USER: conn.getinfo(pyodbc.SQL_USER_NAME)}
    except pyodbc.Error:
        tags = {}
    pin = Pin(service=None, tags=tags)
    wrapped = PyODBCTracedConnection(conn, pin=pin)
    pin.onto(wrapped)
    return wrapped


def patch_conn(conn):
    deprecate(
        "patch_conn is deprecated",
        message="patch_conn is deprecated",
        removal_version="3.0.0",
        category=DDTraceDeprecationWarning,
    )
    return _patch_conn(conn)


class PyODBCTracedCursor(TracedCursor):
    pass


class PyODBCTracedConnection(TracedConnection):
    def __init__(self, conn, pin=None, cursor_cls=None):
        if not cursor_cls:
            cursor_cls = PyODBCTracedCursor
        super(PyODBCTracedConnection, self).__init__(conn, pin, config.pyodbc, cursor_cls=cursor_cls)
