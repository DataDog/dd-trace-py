# 3p
import pyodbc
from ddtrace.vendor.wrapt import wrap_function_wrapper

# project
from ..dbapi import TracedConnection, TracedCursor, FetchTracedCursor
from ... import config, Pin
from ...utils.wrappers import unwrap


config._add(
    "pyodbc",
    dict(
        _default_service="pyodbc",
    ),
)


def patch():
    if getattr(pyodbc, "_datadog_patch", False):
        return
    setattr(pyodbc, "_datadog_patch", True)
    wrap_function_wrapper("pyodbc", "connect", _connect)


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
    def executemany(self, *args, **kwargs):
        super(PyODBCTracedCursor, self).executemany(*args, **kwargs)
        return self

    def execute(self, *args, **kwargs):
        super(PyODBCTracedCursor, self).execute(*args, **kwargs)
        return self


class PyODBCTracedFetchCursor(PyODBCTracedCursor, FetchTracedCursor):
    pass


class PyODBCTracedConnection(TracedConnection):
    def __init__(self, conn, pin=None, cursor_cls=None):
        if not cursor_cls:
            cursor_cls = PyODBCTracedCursor
        super(PyODBCTracedConnection, self).__init__(conn, pin, config.pyodbc, cursor_cls=cursor_cls)

    def execute(self, *args, **kwargs):
        return self.execute(*args, **kwargs)
