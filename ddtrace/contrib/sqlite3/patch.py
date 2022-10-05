import os
import sqlite3
import sqlite3.dbapi2
import sys

from ddtrace import config
from ddtrace.vendor import wrapt

from ...contrib.dbapi import FetchTracedCursor
from ...contrib.dbapi import TracedConnection
from ...contrib.dbapi import TracedCursor
from ...internal.utils.formats import asbool
from ...pin import Pin


# Original connect method
_connect = sqlite3.connect

config._add(
    "sqlite",
    dict(
        _default_service="sqlite",
        _dbapi_span_name_prefix="sqlite",
        trace_fetch_methods=asbool(os.getenv("DD_SQLITE_TRACE_FETCH_METHODS", default=False)),
    ),
)


def patch():
    wrapped = wrapt.FunctionWrapper(_connect, traced_connect)

    setattr(sqlite3, "connect", wrapped)
    setattr(sqlite3.dbapi2, "connect", wrapped)


def unpatch():
    sqlite3.connect = _connect
    sqlite3.dbapi2.connect = _connect


def traced_connect(func, _, args, kwargs):
    conn = func(*args, **kwargs)
    return patch_conn(conn)


def patch_conn(conn):
    wrapped = TracedSQLite(conn)
    Pin().onto(wrapped)
    return wrapped


class TracedSQLiteCursor(TracedCursor):
    def executemany(self, *args, **kwargs):
        # DEV: SQLite3 Cursor.execute always returns back the cursor instance
        super(TracedSQLiteCursor, self).executemany(*args, **kwargs)
        return self

    def execute(self, *args, **kwargs):
        # DEV: SQLite3 Cursor.execute always returns back the cursor instance
        super(TracedSQLiteCursor, self).execute(*args, **kwargs)
        return self


class TracedSQLiteFetchCursor(TracedSQLiteCursor, FetchTracedCursor):
    pass


class TracedSQLite(TracedConnection):
    def __init__(self, conn, pin=None, cursor_cls=None):
        if not cursor_cls:
            # Do not trace `fetch*` methods by default
            cursor_cls = TracedSQLiteFetchCursor if config.sqlite.trace_fetch_methods else TracedSQLiteCursor

            super(TracedSQLite, self).__init__(conn, pin=pin, cfg=config.sqlite, cursor_cls=cursor_cls)

    def execute(self, *args, **kwargs):
        # sqlite has a few extra sugar functions
        return self.cursor().execute(*args, **kwargs)

    # backup was added in Python 3.7
    if sys.version_info >= (3, 7, 0):

        def backup(self, target, *args, **kwargs):
            # sqlite3 checks the type of `target`, it cannot be a wrapped connection
            # https://github.com/python/cpython/blob/4652093e1b816b78e9a585d671a807ce66427417/Modules/_sqlite/connection.c#L1897-L1899
            if isinstance(target, TracedConnection):
                target = target.__wrapped__
            return self.__wrapped__.backup(target, *args, **kwargs)
