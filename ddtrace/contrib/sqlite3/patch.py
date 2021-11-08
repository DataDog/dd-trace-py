import sqlite3
import sqlite3.dbapi2

from ddtrace import config
from ddtrace.vendor import wrapt

from ...contrib.dbapi import FetchTracedCursor
from ...contrib.dbapi import TracedConnection
from ...contrib.dbapi import TracedCursor
from ...internal.utils.formats import asbool
from ...internal.utils.formats import get_env
from ...pin import Pin


# Original connect method
_connect = sqlite3.connect

config._add(
    "sqlite",
    dict(
        _default_service="sqlite",
        trace_fetch_methods=asbool(get_env("sqlite", "trace_fetch_methods", default=False)),
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
    Pin(app="sqlite").onto(wrapped)
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
