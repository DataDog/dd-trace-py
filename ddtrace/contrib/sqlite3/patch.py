# stdlib
import logging

# 3p
import sqlite3
import sqlite3.dbapi2
import wrapt

# project
from ddtrace import Pin
from ddtrace.contrib.dbapi import TracedConnection


log = logging.getLogger(__name__)


def patch():
    """
    patch monkey patches psycopg's connection class so all

    new connections will be traced by default.
    """
    wrapped = wrapt.FunctionWrapper(sqlite3.connect, _connect)

    setattr(sqlite3, 'connect', wrapped)
    setattr(sqlite3.dbapi2, 'connect', wrapped)

def unpatch():
    """ unpatch undoes any monkeypatching. """
    connect = getattr(_connect, 'datadog_patched_func', None)
    if connect is not None:
        sqlite3.connect = connect

def patch_conn(conn, pin=None):
    if not pin:
        pin = Pin(service="sqlite", app="sqlite")
    wrapped = TracedSQLite(conn)
    pin.onto(wrapped)
    return wrapped


# patch functions


class TracedSQLite(TracedConnection):

    def execute(self, *args, **kwargs):
        # sqlite has a few extra sugar functions
        return self.cursor().execute(*args, **kwargs)


def _connect(func, _, args, kwargs):
    conn = func(*args, **kwargs)
    return patch_conn(conn)


