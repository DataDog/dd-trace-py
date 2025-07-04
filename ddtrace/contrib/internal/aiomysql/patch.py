from typing import Dict

import aiomysql
import wrapt

from ddtrace import config
from ddtrace.constants import _SPAN_MEASURED_KEY
from ddtrace.constants import SPAN_KIND
from ddtrace.contrib import dbapi
from ddtrace.contrib import trace_utils
from ddtrace.contrib.internal.trace_utils import _convert_to_string
from ddtrace.ext import SpanKind
from ddtrace.ext import SpanTypes
from ddtrace.ext import db
from ddtrace.ext import net
from ddtrace.internal import core
from ddtrace.internal.constants import COMPONENT
from ddtrace.internal.schema import schematize_database_operation
from ddtrace.internal.schema import schematize_service_name
from ddtrace.internal.utils.wrappers import unwrap
from ddtrace.propagation._database_monitoring import _DBM_Propagator
from ddtrace.trace import Pin


config._add(
    "aiomysql",
    dict(
        _default_service=schematize_service_name("mysql"),
        _dbm_propagator=_DBM_Propagator(0, "query"),
    ),
)


def get_version():
    # type: () -> str
    return getattr(aiomysql, "__version__", "")


def _supported_versions() -> Dict[str, str]:
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

    c = AIOTracedConnection(conn)
    Pin(tags=tags).onto(c)
    return c


class AIOTracedCursor(wrapt.ObjectProxy):
    """TracedCursor wraps a aiomysql cursor and traces its queries."""

    def __init__(self, cursor, pin):
        super(AIOTracedCursor, self).__init__(cursor)
        pin.onto(self)
        self._self_datadog_name = schematize_database_operation("mysql.query", database_provider="mysql")

    async def _trace_method(self, method, resource, extra_tags, *args, **kwargs):
        pin = Pin.get_from(self)
        if not pin or not pin.enabled():
            result = await method(*args, **kwargs)
            return result

        with pin.tracer.trace(
            self._self_datadog_name,
            service=trace_utils.ext_service(pin, config.aiomysql),
            resource=resource,
            span_type=SpanTypes.SQL,
        ) as s:
            s.set_tag_str(COMPONENT, config.aiomysql.integration_name)

            # set span.kind to the type of request being performed
            s.set_tag_str(SPAN_KIND, SpanKind.CLIENT)

            s.set_tag(_SPAN_MEASURED_KEY)
            s.set_tags(pin.tags)
            s.set_tags(extra_tags)

            # dispatch DBM
            result = core.dispatch_with_results("aiomysql.execute", (config.aiomysql, s, args, kwargs)).result
            if result:
                s, args, kwargs = result.value

            try:
                result = await method(*args, **kwargs)
                return result
            finally:
                s.set_metric(db.ROWCOUNT, self.rowcount)
                s.set_metric("db.rownumber", self.rownumber)

    async def executemany(self, query, *args, **kwargs):
        result = await self._trace_method(
            self.__wrapped__.executemany, query, {"sql.executemany": "true"}, query, *args, **kwargs
        )
        return result

    async def execute(self, query, *args, **kwargs):
        result = await self._trace_method(self.__wrapped__.execute, query, {}, query, *args, **kwargs)
        return result

    # Explicitly define `__aenter__` and `__aexit__` since they do not get proxied properly
    async def __aenter__(self):
        # The base class just returns `self`, but we want the wrapped cursor so we return ourselves
        return self

    async def __aexit__(self, *args, **kwargs):
        return await self.__wrapped__.__aexit__(*args, **kwargs)


class AIOTracedConnection(wrapt.ObjectProxy):
    def __init__(self, conn, pin=None, cursor_cls=AIOTracedCursor):
        super(AIOTracedConnection, self).__init__(conn)
        name = dbapi._get_vendor(conn)
        db_pin = pin or Pin(service=name)
        db_pin.onto(self)
        # wrapt requires prefix of `_self` for attributes that are only in the
        # proxy (since some of our source objects will use `__slots__`)
        self._self_cursor_cls = cursor_cls

    def cursor(self, *args, **kwargs):
        ctx_manager = self.__wrapped__.cursor(*args, **kwargs)
        pin = Pin.get_from(self)
        if not pin:
            return ctx_manager

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
            return self._self_cursor_cls(cursor, pin)

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
