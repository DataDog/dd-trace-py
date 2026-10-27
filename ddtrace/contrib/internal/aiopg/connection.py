from typing import Any
from typing import Callable
from typing import Optional

from aiopg import __version__
import wrapt

from ddtrace import config
from ddtrace._trace.pin import Pin
from ddtrace.constants import _SPAN_MEASURED_KEY
from ddtrace.constants import SPAN_KIND
from ddtrace.contrib import trace_utils
from ddtrace.contrib._events.dbapi import DbQueryEvent
from ddtrace.contrib.internal.psycopg.cursor import psycopg2_python_encoding
from ddtrace.contrib.internal.trace_utils import set_service_and_source
from ddtrace.ext import SpanKind
from ddtrace.ext import SpanTypes
from ddtrace.ext import db
from ddtrace.internal import core
from ddtrace.internal.constants import COMPONENT
from ddtrace.internal.schema import schematize_database_operation
from ddtrace.internal.utils.version import parse_version
from ddtrace.trace import tracer


AIOPG_VERSION = parse_version(__version__)


def _wrap_context_manager(context: Any, wrap_result: Callable[[Any], Any]) -> Any:
    original = context._coro

    async def traced_result() -> Any:
        return wrap_result(await original)

    coro = traced_result()
    # aiopg 0.16 drives its coroutine with yield from; newer versions use await.
    context._coro = coro.__await__() if AIOPG_VERSION < (1, 0) else coro
    return context


class AIOTracedCursor(wrapt.ObjectProxy):
    """TracedCursor wraps a psql cursor and traces its queries."""

    def __init__(self, cursor, pin):
        super().__init__(cursor)
        pin.onto(self)
        self._datadog_name = schematize_database_operation("postgres.query", database_provider="postgresql")

    def _dbapi_query_encoding(self) -> str:
        return psycopg2_python_encoding(self.__wrapped__.connection)

    async def _trace_method(self, method, resource, extra_tags, *args, **kwargs):
        pin = Pin.get_from(self)
        if not pin or not pin.enabled():
            result = await method(*args, **kwargs)
            return result

        with tracer.trace(
            self._datadog_name,
            resource=resource,
            span_type=SpanTypes.SQL,
        ) as s:
            set_service_and_source(s, trace_utils.ext_service(pin, config.aiopg), config.aiopg)
            s._set_attribute(COMPONENT, config.aiopg.integration_name)
            s._set_attribute(db.SYSTEM, "postgresql")

            # set span.kind to the type of request being performed
            s._set_attribute(SPAN_KIND, SpanKind.CLIENT)

            s._set_attribute(_SPAN_MEASURED_KEY, 1)
            s.set_tags(pin.tags)
            s.set_tags(extra_tags)

            try:
                result = await method(*args, **kwargs)
                return result
            finally:
                s._set_attribute(db.ROWCOUNT, self.rowcount)

    async def executemany(self, query, *args, **kwargs):
        # FIXME[matt] properly handle kwargs here. arg names can be different
        # with different libs.
        if isinstance(query, (str, bytes, bytearray, memoryview)):
            core.dispatch_event(
                DbQueryEvent(query=query, span_name_prefix="postgres", get_encoding=self._dbapi_query_encoding)
            )
        result = await self._trace_method(
            self.__wrapped__.executemany, query, {"sql.executemany": "true"}, query, *args, **kwargs
        )
        return result

    async def execute(self, query, *args, **kwargs):
        if isinstance(query, (str, bytes, bytearray, memoryview)):
            core.dispatch_event(
                DbQueryEvent(query=query, span_name_prefix="postgres", get_encoding=self._dbapi_query_encoding)
            )
        result = await self._trace_method(self.__wrapped__.execute, query, {}, query, *args, **kwargs)
        return result

    async def callproc(self, proc, args):
        result = await self._trace_method(self.__wrapped__.callproc, proc, {}, proc, args)
        return result

    def __aiter__(self):
        return self.__wrapped__.__aiter__()


class AIOTracedConnection(wrapt.ObjectProxy):
    """TracedConnection wraps a Connection with tracing code."""

    def __init__(
        self,
        conn: Any,
        pin: Optional[Pin] = None,
        cursor_cls: type[AIOTracedCursor] = AIOTracedCursor,
        db_tags: Optional[dict[str, str]] = None,
    ) -> None:
        super().__init__(conn)
        db_pin = pin or Pin(tags=db_tags)
        db_pin.onto(self)
        # wrapt requires prefix of `_self` for attributes that are only in the
        # proxy (since some of our source objects will use `__slots__`)
        self._self_cursor_cls = cursor_cls

    def cursor(self, *args: Any, **kwargs: Any) -> Any:
        return _wrap_context_manager(self.__wrapped__.cursor(*args, **kwargs), self._trace_cursor)

    def _trace_cursor(self, cursor: Any) -> Any:
        pin = Pin.get_from(self)
        if not pin:
            return cursor
        return self._self_cursor_cls(cursor, pin)
