# 3p
import sqlite3
import sqlite3.dbapi2
import wrapt

# project
from ddtrace import Pin
from ddtrace.contrib.dbapi import TracedConnection

from ...ext import AppTypes

# Original connect method
_connect = sqlite3.connect

def patch():
    wrapped = wrapt.FunctionWrapper(_connect, traced_connect)

    setattr(sqlite3, 'connect', wrapped)
    setattr(sqlite3.dbapi2, 'connect', wrapped)

def unpatch():
    sqlite3.connect = _connect
    sqlite3.dbapi2.connect = _connect

def traced_connect(func, _, args, kwargs):
    conn = func(*args, **kwargs)
    return patch_conn(conn)

def patch_conn(conn):
    wrapped = TracedSQLite(conn)
    Pin(service="sqlite", app="sqlite", app_type=AppTypes.db).onto(wrapped)
    return wrapped

class TracedSQLite(TracedConnection):

    def execute(self, *args, **kwargs):
        # sqlite has a few extra sugar functions
        return self.cursor().execute(*args, **kwargs)
