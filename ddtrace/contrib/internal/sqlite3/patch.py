from collections.abc import Callable
import sqlite3
import sqlite3.dbapi2
from types import BuiltinMethodType
from typing import Any
from typing import Optional

from wrapt import wrap_function_wrapper as _w

from ddtrace import config
from ddtrace.contrib.dbapi import FetchTracedCursor
from ddtrace.contrib.dbapi import TracedConnection
from ddtrace.contrib.dbapi import TracedCursor
from ddtrace.contrib.dbapi import _dispatch_query_event
from ddtrace.contrib.dbapi import _set_query_rowcount
from ddtrace.contrib.dbapi import _set_query_span_tags
from ddtrace.contrib.internal.trace_utils import ext_service
from ddtrace.contrib.internal.trace_utils import is_tracing_enabled
from ddtrace.ext import SpanTypes
from ddtrace.ext import db
from ddtrace.internal import core
from ddtrace.internal.schema import schematize_database_operation
from ddtrace.internal.schema import schematize_service_name
from ddtrace.internal.settings import env
from ddtrace.internal.utils.formats import asbool
from ddtrace.internal.utils.wrappers import unwrap as _u
from ddtrace.trace import tracer


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

    core.dispatch("sql.patch")


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

    def executescript(self, *args: Any, **kwargs: Any) -> "TracedSQLiteCursor":
        # Let the driver reject invalid scripts and signatures before inspection.
        if len(args) != 1 or kwargs or not isinstance(args[0], str):
            return self.__wrapped__.executescript(*args, **kwargs)
        sql_script = args[0]
        self._self_last_execute_operation = sql_script
        # Inspect the entire script before SQLite can commit or execute its first statement.
        self._dispatch_query_event(sql_script)
        self._trace_method(self.__wrapped__.executescript, self._self_datadog_name, sql_script, {}, None, sql_script)
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

    def _dbapi_query_encoding(self) -> Optional[str]:
        return None

    def _trace_connection_query(
        self, method: Callable[..., Any], args: tuple[Any, ...], kwargs: dict[str, Any], bulk: bool = False
    ) -> Any:
        """Trace the original shortcut so connection overrides and custom results survive."""
        expected_args = 2 if bulk else 1
        if not args or not isinstance(args[0], str):
            return method(*args, **kwargs)
        # Preserve native binding errors without restricting custom connection signatures.
        if isinstance(method, BuiltinMethodType) and (len(args) != expected_args or kwargs):
            return method(*args, **kwargs)
        query = args[0]
        _dispatch_query_event(query, "sqlite", self._dbapi_query_encoding)
        if not is_tracing_enabled():
            result = method(*args, **kwargs)
        else:
            extra_tags = {"sql.executemany": "true"} if bulk else {}
            with tracer.trace(
                self._self_config._dbapi_span_operation_name,
                service=ext_service(None, self._self_config),
                resource=query,
                span_type=SpanTypes.SQL,
            ) as span:
                _set_query_span_tags(span, self._self_config, self._self_db_tags, extra_tags)
                core.dispatch("db_query_check", (args, kwargs, self._self_config.integration_name, method))
                result = method(*args, **kwargs)
                if isinstance(result, sqlite3.Cursor):
                    _set_query_rowcount(span, result)
        if isinstance(result, sqlite3.Cursor) and not isinstance(result, TracedCursor):
            result = self._self_cursor_cls(result, cfg=self._self_config, db_tags=self._self_db_tags)
            result._self_last_execute_operation = query
        return result

    def executemany(self, *args: Any, **kwargs: Any) -> Any:
        return self._trace_connection_query(self.__wrapped__.executemany, args, kwargs, bulk=True)

    def executescript(self, *args: Any, **kwargs: Any) -> Any:
        return self._trace_connection_query(self.__wrapped__.executescript, args, kwargs)

    def backup(self, target, *args, **kwargs):
        # sqlite3 checks the type of `target`, it cannot be a wrapped connection
        # https://github.com/python/cpython/blob/4652093e1b816b78e9a585d671a807ce66427417/Modules/_sqlite/connection.c#L1897-L1899
        if isinstance(target, TracedConnection):
            target = target.__wrapped__
        return self.__wrapped__.backup(target, *args, **kwargs)
