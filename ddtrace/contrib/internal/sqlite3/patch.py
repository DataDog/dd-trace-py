import sqlite3
import sqlite3.dbapi2

from wrapt import wrap_function_wrapper as _w

from ddtrace import config
from ddtrace.contrib.dbapi import FetchTracedCursor
from ddtrace.contrib.dbapi import TracedConnection
from ddtrace.contrib.dbapi import TracedCursor
from ddtrace.ext import db
from ddtrace.internal import core
from ddtrace.internal.schema import schematize_database_operation
from ddtrace.internal.schema import schematize_service_name
from ddtrace.internal.settings import env
from ddtrace.internal.utils.formats import asbool
from ddtrace.internal.utils.wrappers import unwrap as _u


config._add(
    "sqlite",
    dict(
        _default_service=schematize_service_name("sqlite"),
        _dbapi_span_name_prefix="sqlite",
        _dbapi_span_operation_name=schematize_database_operation("sqlite.query", database_provider="sqlite"),
        trace_fetch_methods=asbool(env.get("DD_SQLITE_TRACE_FETCH_METHODS", default=False)),
    ),
)


def get_version() -> str:
    return sqlite3.sqlite_version


def _supported_versions() -> dict[str, str]:
    return {"sqlite3": "*"}


def patch():
    if getattr(sqlite3, "_datadog_patch", False):
        return
    sqlite3._datadog_patch = True

    _w(sqlite3, "connect", traced_connect)
    _w(sqlite3.dbapi2, "connect", traced_connect)

    core.dispatch("sqlite3.patch", ())


def unpatch():
    if getattr(sqlite3, "_datadog_patch", False):
        sqlite3._datadog_patch = False

        _u(sqlite3, "connect")
        _u(sqlite3.dbapi2, "connect")


def traced_connect(func, _, args, kwargs):
    conn = func(*args, **kwargs)
    return TracedSQLite(conn)


class TracedSQLiteCursor(TracedCursor):
    def executemany(self, *args, **kwargs):
        # DEV: SQLite3 Cursor.execute always returns back the cursor instance
        super().executemany(*args, **kwargs)
        return self

    def execute(self, *args, **kwargs):
        # DEV: SQLite3 Cursor.execute always returns back the cursor instance
        super().execute(*args, **kwargs)
        return self


class TracedSQLiteFetchCursor(TracedSQLiteCursor, FetchTracedCursor):
    pass


class TracedSQLite(TracedConnection):
    def __init__(self, conn, cursor_cls=None):
        if not cursor_cls:
            # Do not trace `fetch*` methods by default
            cursor_cls = TracedSQLiteFetchCursor if config.sqlite.trace_fetch_methods else TracedSQLiteCursor
            super().__init__(conn, cfg=config.sqlite, cursor_cls=cursor_cls, db_tags={db.SYSTEM: "sqlite"})

    def execute(self, *args, **kwargs):
        # sqlite has a few extra sugar functions
        return self.cursor().execute(*args, **kwargs)

    def backup(self, target, *args, **kwargs):
        # sqlite3 checks the type of `target`, it cannot be a wrapped connection
        # https://github.com/python/cpython/blob/4652093e1b816b78e9a585d671a807ce66427417/Modules/_sqlite/connection.c#L1897-L1899
        if isinstance(target, TracedConnection):
            target = target.__wrapped__
        return self.__wrapped__.backup(target, *args, **kwargs)
