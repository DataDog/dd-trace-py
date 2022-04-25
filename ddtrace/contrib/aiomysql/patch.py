import aiomysql

from ddtrace import Pin
from ddtrace import config
from ddtrace.constants import ANALYTICS_SAMPLE_RATE_KEY
from ddtrace.constants import SPAN_MEASURED_KEY
from ddtrace.contrib import dbapi
from ddtrace.ext import sql
from ddtrace.internal.utils.wrappers import unwrap
from ddtrace.vendor import wrapt

from ...ext import SpanTypes
from ...ext import db
from ...ext import net


config._add(
    "aiomysql",
    dict(_default_service="mysql"),
)

CONN_ATTR_BY_TAG = {
    net.TARGET_HOST: "host",
    net.TARGET_PORT: "port",
    db.USER: "user",
    db.NAME: "db",
}


async def patched_connect(connect_func, _, args, kwargs):
    conn = await connect_func(*args, **kwargs)
    tags = {}
    for tag, attr in CONN_ATTR_BY_TAG.items():
        if hasattr(conn, attr):
            tags[tag] = getattr(conn, attr)

    c = AIOTracedConnection(conn)
    Pin(tags=tags).onto(c)
    return c


class AIOTracedCursor(wrapt.ObjectProxy):
    """TracedCursor wraps a aiomysql cursor and traces its queries."""

    def __init__(self, cursor, pin):
        super(AIOTracedCursor, self).__init__(cursor)
        pin.onto(self)
        self._self_datadog_name = "mysql.query"

    async def _trace_method(self, method, resource, extra_tags, *args, **kwargs):
        pin = Pin.get_from(self)
        if not pin or not pin.enabled():
            result = await method(*args, **kwargs)
            return result
        service = pin.service

        with pin.tracer.trace(
            self._self_datadog_name, service=service, resource=resource, span_type=SpanTypes.SQL
        ) as s:
            s.set_tag(SPAN_MEASURED_KEY)
            s.set_tag(sql.QUERY, resource)
            s.set_tags(pin.tags)
            s.set_tags(extra_tags)

            # set analytics sample rate
            s.set_tag(ANALYTICS_SAMPLE_RATE_KEY, config.aiomysql.get_analytics_sample_rate())

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


class AIOTracedConnection(wrapt.ObjectProxy):
    def __init__(self, conn, pin=None, cursor_cls=AIOTracedCursor):
        super(AIOTracedConnection, self).__init__(conn)
        name = dbapi._get_vendor(conn)
        db_pin = pin or Pin(service=name)
        db_pin.onto(self)
        # wrapt requires prefix of `_self` for attributes that are only in the
        # proxy (since some of our source objects will use `__slots__`)
        self._self_cursor_cls = cursor_cls

    async def cursor(self, *args, **kwargs):
        cursor = await self.__wrapped__.cursor(*args, **kwargs)
        pin = Pin.get_from(self)
        if not pin:
            return cursor
        return self._self_cursor_cls(cursor, pin)

    async def __aenter__(self):
        return self.__wrapped__.__aenter__()


def patch():
    if getattr(aiomysql, "__datadog_patch", False):
        return
    setattr(aiomysql, "__datadog_patch", True)
    wrapt.wrap_function_wrapper(aiomysql.connection, "_connect", patched_connect)


def unpatch():
    if getattr(aiomysql, "__datadog_patch", False):
        setattr(aiomysql, "__datadog_patch", False)
        unwrap(aiomysql.connection, "_connect")
