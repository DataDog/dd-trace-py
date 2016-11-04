# stdlib
import logging

# 3p
import sqlite3
import sqlite3.dbapi2
import wrapt

# project
from ddtrace.contrib.dbapi import TracedConnection


log = logging.getLogger(__name__)


def _connect(connect_func, _, args, kwargs):
    db = connect_func(*args, **kwargs)
    return TracedConnection(db)

def unpatch():
    """ unpatch undoes any monkeypatching. """
    connect = getattr(_connect, 'datadog_patched_func', None)
    if connect is not None:
        sqlite3.connect = connect

def patch():
    """
    patch monkey patches psycopg's connection class so all

    new connections will be traced by default.
    """
    wrapped = wrapt.FunctionWrapper(sqlite3.connect, _connect)

    setattr(sqlite3, 'connect', wrapped)
    setattr(sqlite3.dbapi2, 'connect', wrapped)
