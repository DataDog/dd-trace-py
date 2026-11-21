import inspect
from typing import Any
from typing import Callable

from ddtrace import config
from ddtrace.contrib._events.dbapi import DbQueryEvent
from ddtrace.internal import core
from ddtrace.internal.logger import get_logger
from ddtrace.internal.utils import ArgumentError
from ddtrace.internal.utils import get_argument_value

from .dbapi import TracedConnection
from .dbapi import TracedCursor
from .internal.trace_utils import is_tracing_enabled
from .internal.trace_utils import iswrapped


log = get_logger(__name__)


def get_version() -> str:
    return ""


class TracedAsyncCursor(TracedCursor):
    async def __aenter__(self):
        # previous versions of the dbapi didn't support context managers. let's
        # reference the func that would be called to ensure that error
        # messages will be the same.
        await self.__wrapped__.__aenter__()

        return self

    def __aiter__(self):
        return self.__wrapped__.__aiter__()

    async def __aexit__(self, exc_type, exc_val, exc_tb):
        # previous versions of the dbapi didn't support context managers. let's
        # reference the func that would be called to ensure that error
        # messages will be the same.
        return await self.__wrapped__.__aexit__(exc_type, exc_val, exc_tb)

    async def _trace_method(
        self,
        method: Callable[..., Any],
        name: str,
        resource: Any,
        extra_tags: dict[str, Any],
        supports_query_rewriting: bool,
        *args: Any,
        **kwargs: Any,
    ) -> Any:
        if not is_tracing_enabled():
            return await method(*args, **kwargs)
        with self._trace_method_context(method, name, resource, extra_tags, supports_query_rewriting, args, kwargs) as (
            args,
            kwargs,
        ):
            return await method(*args, **kwargs)

    async def executemany(self, query, *args, **kwargs):
        """Wraps the cursor.executemany method"""
        self._self_last_execute_operation = query
        if isinstance(query, (str, bytes, bytearray, memoryview)):
            core.dispatch_event(
                DbQueryEvent(
                    query=query,
                    span_name_prefix=self._self_dbapi_span_name_prefix,
                    get_encoding=self._dbapi_query_encoding,
                )
            )
        # Always return the result as-is
        # DEV: Some libraries return `None`, others `int`, and others the cursor objects
        #      These differences should be overridden at the integration specific layer (e.g. in `sqlite3/patch.py`)
        # FIXME[matt] properly handle kwargs here. arg names can be different
        # with different libs.
        return await self._trace_method(
            self.__wrapped__.executemany,
            self._self_datadog_name,
            query,
            {"sql.executemany": "true"},
            True,
            query,
            *args,
            **kwargs,
        )

    async def execute(self, query, *args, **kwargs):
        """Wraps the cursor.execute method"""
        self._self_last_execute_operation = query
        if isinstance(query, (str, bytes, bytearray, memoryview)):
            core.dispatch_event(
                DbQueryEvent(
                    query=query,
                    span_name_prefix=self._self_dbapi_span_name_prefix,
                    get_encoding=self._dbapi_query_encoding,
                )
            )

        # Always return the result as-is
        # DEV: Some libraries return `None`, others `int`, and others the cursor objects
        #      These differences should be overridden at the integration specific layer (e.g. in `sqlite3/patch.py`)
        return await self._trace_method(
            self.__wrapped__.execute,
            self._self_datadog_name,
            query,
            {},
            True,
            query,
            *args,
            **kwargs,
        )


class FetchTracedAsyncCursor(TracedAsyncCursor):
    """FetchTracedAsyncCursor for psycopg"""

    async def fetchone(self, *args, **kwargs):
        """Wraps the cursor.fetchone method"""
        span_name = "{}.{}".format(self._self_datadog_name, "fetchone")
        return await self._trace_method(
            self.__wrapped__.fetchone, span_name, self._self_last_execute_operation, {}, False, *args, **kwargs
        )

    async def fetchall(self, *args, **kwargs):
        """Wraps the cursor.fetchall method"""
        span_name = "{}.{}".format(self._self_datadog_name, "fetchall")
        return await self._trace_method(
            self.__wrapped__.fetchall, span_name, self._self_last_execute_operation, {}, False, *args, **kwargs
        )

    async def fetchmany(self, *args, **kwargs):
        """Wraps the cursor.fetchmany method"""
        span_name = "{}.{}".format(self._self_datadog_name, "fetchmany")
        # We want to trace the information about how many rows were requested. Note that this number may be larger
        # the number of rows actually returned if less then requested are available from the query.
        size_tag_key = "db.fetch.size"

        try:
            extra_tags = {size_tag_key: get_argument_value(args, kwargs, 0, "size")}
        except ArgumentError:
            default_array_size = getattr(self.__wrapped__, "arraysize", None)
            extra_tags = {size_tag_key: default_array_size} if default_array_size else {}

        return await self._trace_method(
            self.__wrapped__.fetchmany, span_name, self._self_last_execute_operation, extra_tags, False, *args, **kwargs
        )


class TracedAsyncConnection(TracedConnection):
    def __init__(self, conn, pin=None, cfg=config.dbapi2, cursor_cls=None, db_tags=None):
        if not cursor_cls:
            # Do not trace `fetch*` methods by default
            cursor_cls = FetchTracedAsyncCursor if cfg.trace_fetch_methods else TracedAsyncCursor
        super().__init__(conn, pin=pin, cfg=cfg, cursor_cls=cursor_cls, db_tags=db_tags)

    async def __aenter__(self):
        """Context management is not defined by the dbapi spec.

        This means unfortunately that the database clients each define their own
        implementations.

        The ones we know about are:

        - mysqlclient<2.0 which returns a cursor instance. >=2.0 returns a
          connection instance.
        - psycopg returns a connection.
        - pyodbc returns a connection.
        - pymysql doesn't implement it.
        - sqlite3 returns the connection.
        """
        r = await self.__wrapped__.__aenter__()

        if hasattr(r, "cursor"):
            # r is Connection-like.
            if r is self.__wrapped__:
                # Return the reference to this proxy object. Returning r would
                # return the untraced reference.
                return self
            else:
                # r is a different connection object.
                # This should not happen in practice but play it safe so that
                # the original functionality is maintained.
                log.warning(
                    "Unexpected object type returned from __wrapped__.__aenter__()."
                    "Expected a wrapped instance, but received a different object."
                )
                return r
        elif hasattr(r, "execute"):
            # r is Cursor-like.
            if iswrapped(r):
                return r
            return self._self_cursor_cls(r, cfg=self._self_config, db_tags=self._self_db_tags)
        else:
            # Otherwise r is some other object, so maintain the functionality
            # of the original.
            log.warning(
                "Unexpected object type returned from __wrapped__.__aenter__()."
                "Expected a wrapped instance, but received a different object."
            )
            return r

    async def __aexit__(self, exc_type, exc_val, exc_tb):
        # previous versions of the dbapi didn't support context managers. let's
        # reference the func that would be called to ensure that errors
        # messages will be the same.
        return await self.__wrapped__.__aexit__(exc_type, exc_val, exc_tb)

    def cursor(self, *args, **kwargs):
        cursor = self.__wrapped__.cursor(*args, **kwargs)
        # mysql.connector.aio returns an awaitable cursor.
        if inspect.isawaitable(cursor):

            async def _wrap_cursor():
                awaited_cursor = await cursor
                return self._self_cursor_cls(awaited_cursor, cfg=self._self_config, db_tags=self._self_db_tags)

            return _wrap_cursor()

        return self._self_cursor_cls(cursor, cfg=self._self_config, db_tags=self._self_db_tags)

    async def _trace_method(
        self, method: Callable[..., Any], name: str, extra_tags: dict[str, Any], *args: Any, **kwargs: Any
    ) -> Any:
        if not is_tracing_enabled():
            return await method(*args, **kwargs)

        with core.context_with_event(self._trace_event(name, extra_tags)):
            return await method(*args, **kwargs)

    async def commit(self, *args, **kwargs):
        span_name = "{}.{}".format(self._self_datadog_name, "commit")
        return await self._trace_method(self.__wrapped__.commit, span_name, {}, *args, **kwargs)

    async def rollback(self, *args, **kwargs):
        span_name = "{}.{}".format(self._self_datadog_name, "rollback")
        return await self._trace_method(self.__wrapped__.rollback, span_name, {}, *args, **kwargs)
