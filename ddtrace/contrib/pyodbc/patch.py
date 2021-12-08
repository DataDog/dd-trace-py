import pyodbc

from ... import Pin
from ... import config
from ...internal.utils.formats import asbool
from ...internal.utils.formats import get_env
from ..dbapi import TracedConnection
from ..dbapi import TracedCursor
from ..trace_utils import unwrap
from ..trace_utils import wrap


config._add(
    "pyodbc",
    dict(
        _default_service="pyodbc",
        trace_fetch_methods=asbool(get_env("pyodbc", "trace_fetch_methods", default=False)),
    ),
)


def patch():
    if getattr(pyodbc, "_datadog_patch", False):
        return
    setattr(pyodbc, "_datadog_patch", True)
    wrap("pyodbc", "connect", _connect)


def unpatch():
    if getattr(pyodbc, "_datadog_patch", False):
        setattr(pyodbc, "_datadog_patch", False)
        unwrap(pyodbc, "connect")


def _connect(func, instance, args, kwargs):
    conn = func(*args, **kwargs)
    return patch_conn(conn)


def patch_conn(conn):
    pin = Pin(service=None, app="pyodbc")
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
