from typing import Any
from typing import Callable

import aiomysql
import wrapt

from ddtrace import config
from ddtrace.contrib import trace_utils
from ddtrace.contrib._events.dbapi import DatabaseDbmInjectionEvent
from ddtrace.contrib._events.dbapi import DatabaseOperationEvent
from ddtrace.contrib._events.dbapi import DatabaseResultEvent
from ddtrace.contrib._events.dbapi import DbQueryEvent
from ddtrace.contrib.internal.trace_utils import _convert_to_string
from ddtrace.contrib.internal.trace_utils import is_tracing_enabled
from ddtrace.ext import db
from ddtrace.ext import net
from ddtrace.internal import core
from ddtrace.internal.schema import schematize_database_operation
from ddtrace.internal.schema import schematize_service_name
from ddtrace.internal.span_bus import span_from_context
from ddtrace.internal.utils.wrappers import unwrap
from ddtrace.propagation._database_monitoring import _DBM_Propagator


config._add(
    "aiomysql",
    dict(
        _default_service=schematize_service_name("mysql"),
        _dbm_propagator=_DBM_Propagator(0, "query"),
    ),
)


def get_version() -> str:
    return getattr(aiomysql, "__version__", "")


def _supported_versions() -> dict[str, str]:
    return {"aiomysql": ">=0.1.0"}


CONN_ATTR_BY_TAG = {
    net.TARGET_HOST: "host",
    net.TARGET_PORT: "port",
    net.SERVER_ADDRESS: "host",
    db.USER: "user",
    db.NAME: "db",
}


async def patched_connect(connect_func, _, args, kwargs):
    conn = await connect_func(*args, **kwargs)
    tags = {}
    for tag, attr in CONN_ATTR_BY_TAG.items():
        if hasattr(conn, attr):
            tags[tag] = _convert_to_string(getattr(conn, attr, None))
    tags[db.SYSTEM] = "mysql"

    return AIOTracedConnection(conn, db_tags=tags)


class AIOTracedCursor(wrapt.ObjectProxy):
    """TracedCursor wraps a aiomysql cursor and traces its queries."""

    def __init__(self, cursor, db_tags):
        super().__init__(cursor)
        self._self_datadog_name = schematize_database_operation("mysql.query", database_provider="mysql")
        self._self_db_tags = db_tags

    def _dbapi_query_encoding(self) -> str:
        return self.__wrapped__.connection.encoding

    async def _trace_method(
        self, method: Callable[..., Any], resource: Any, extra_tags: dict[str, Any], *args: Any, **kwargs: Any
    ) -> Any:
        if not is_tracing_enabled():
            result = await method(*args, **kwargs)
            return result

        event = DatabaseOperationEvent(
            component=config.aiomysql.integration_name,
            integration_config=config.aiomysql,
            operation_name=self._self_datadog_name,
            resource=resource,
            service=trace_utils.ext_service(None, config.aiomysql),
            service_from_config=True,
            metadata_before_tags=True,
            connection_tags=self._self_db_tags,
            tags=dict(extra_tags),
        )
        with core.context_with_event(event) as ctx:
            # dispatch DBM
            injection = DatabaseDbmInjectionEvent(context=ctx, args=args, kwargs=kwargs)
            core.dispatch_event(injection)
            args, kwargs = injection.args, injection.kwargs

            try:
                result = await method(*args, **kwargs)
                return result
            finally:
                core.dispatch_event(
                    DatabaseResultEvent(
                        span=span_from_context(ctx),
                        row_count=self.rowcount,
                        row_number=self.rownumber,
                        skip_none_row_count=False,
                    ),
                    allow_raise=True,
                )

    async def executemany(self, query, *args, **kwargs):
        if isinstance(query, (str, bytes, bytearray, memoryview)):
            core.dispatch_event(
                DbQueryEvent(query=query, span_name_prefix="mysql", get_encoding=self._dbapi_query_encoding)
            )
        result = await self._trace_method(
            self.__wrapped__.executemany, query, {"sql.executemany": "true"}, query, *args, **kwargs
        )
        return result

    async def execute(self, query, *args, **kwargs):
        if isinstance(query, (str, bytes, bytearray, memoryview)):
            core.dispatch_event(
                DbQueryEvent(query=query, span_name_prefix="mysql", get_encoding=self._dbapi_query_encoding)
            )
        result = await self._trace_method(self.__wrapped__.execute, query, {}, query, *args, **kwargs)
        return result

    # Explicitly define `__aenter__` and `__aexit__` since they do not get proxied properly
    async def __aenter__(self):
        # The base class just returns `self`, but we want the wrapped cursor so we return ourselves
        return self

    async def __aexit__(self, *args, **kwargs):
        return await self.__wrapped__.__aexit__(*args, **kwargs)


class AIOTracedConnection(wrapt.ObjectProxy):
    def __init__(self, conn, db_tags, cursor_cls=AIOTracedCursor):
        super().__init__(conn)
        # wrapt requires prefix of `_self` for attributes that are only in the
        # proxy (since some of our source objects will use `__slots__`)
        self._self_cursor_cls = cursor_cls
        self._self_db_tags = db_tags

    def cursor(self, *args, **kwargs):
        ctx_manager = self.__wrapped__.cursor(*args, **kwargs)

        # The result of `cursor()` is an `aiomysql.utils._ContextManager`
        #   which wraps a coroutine (a future) and adds async context manager
        #   helper functions to it.
        # https://github.com/aio-libs/aiomysql/blob/8a32f052a16dc3886af54b98f4d91d95862bfb8e/aiomysql/connection.py#L461
        # https://github.com/aio-libs/aiomysql/blob/7fa5078da31bbc95f5e32a934a4b2b4207c67ede/aiomysql/utils.py#L30-L79
        # We cannot swap out the result on the future/context manager so
        #   instead we have to create a new coroutine that returns our
        #   wrapped cursor
        # We also cannot turn `def cursor` into `async def cursor` because
        #   otherwise we will change the result to be a coroutine instead of
        #   an `aiomysql.utils._ContextManager` which wraps a coroutine. This
        #   will cause issues with `async with conn.cursor() as cur:` usage.
        async def _wrap_cursor():
            cursor = await ctx_manager
            return self._self_cursor_cls(cursor, self._self_db_tags)

        return type(ctx_manager)(_wrap_cursor())

    # Explicitly define `__aenter__` and `__aexit__` since they do not get proxied properly
    async def __aenter__(self):
        return await self.__wrapped__.__aenter__()

    async def __aexit__(self, *args, **kwargs):
        return await self.__wrapped__.__aexit__(*args, **kwargs)


def patch():
    if getattr(aiomysql, "__datadog_patch", False):
        return
    aiomysql.__datadog_patch = True
    wrapt.wrap_function_wrapper(aiomysql.connection, "_connect", patched_connect)


def unpatch():
    if getattr(aiomysql, "__datadog_patch", False):
        aiomysql.__datadog_patch = False
        unwrap(aiomysql.connection, "_connect")
