from aiopg import __version__
from aiopg.utils import _ContextManager
import wrapt

from ddtrace import config
from ddtrace.constants import _SPAN_MEASURED_KEY
from ddtrace.constants import SPAN_KIND
from ddtrace.contrib import trace_utils
from ddtrace.contrib._events.dbapi import DbQueryEvent
from ddtrace.contrib.internal.trace_utils import is_tracing_enabled
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


class AIOTracedCursor(wrapt.ObjectProxy):
    """TracedCursor wraps a psql cursor and traces its queries."""

    def __init__(self, cursor, db_tags):
        super(AIOTracedCursor, self).__init__(cursor)
        self._datadog_name = schematize_database_operation("postgres.query", database_provider="postgresql")
        self._self_db_tags = db_tags

    async def _trace_method(self, method, resource, extra_tags, *args, **kwargs):
        if not is_tracing_enabled():
            result = await method(*args, **kwargs)
            return result

        with tracer.trace(
            self._datadog_name,
            resource=resource,
            span_type=SpanTypes.SQL,
        ) as s:
            set_service_and_source(s, trace_utils.ext_service(None, config.aiopg), config.aiopg)
            s._set_attribute(COMPONENT, config.aiopg.integration_name)
            s._set_attribute(db.SYSTEM, "postgresql")

            # set span.kind to the type of request being performed
            s._set_attribute(SPAN_KIND, SpanKind.CLIENT)

            s._set_attribute(_SPAN_MEASURED_KEY, 1)
            s.set_tags(self._self_db_tags)
            s.set_tags(extra_tags)

            try:
                result = await method(*args, **kwargs)
                return result
            finally:
                s._set_attribute(db.ROWCOUNT, self.rowcount)

    async def executemany(self, query, *args, **kwargs):
        # FIXME[matt] properly handle kwargs here. arg names can be different
        # with different libs.
        if isinstance(query, str):
            core.dispatch_event(DbQueryEvent(query=query, span_name_prefix="postgres"))
        result = await self._trace_method(
            self.__wrapped__.executemany, query, {"sql.executemany": "true"}, query, *args, **kwargs
        )
        return result

    async def execute(self, query, *args, **kwargs):
        if isinstance(query, str):
            core.dispatch_event(DbQueryEvent(query=query, span_name_prefix="postgres"))
        result = await self._trace_method(self.__wrapped__.execute, query, {}, query, *args, **kwargs)
        return result

    async def callproc(self, proc, args):
        result = await self._trace_method(self.__wrapped__.callproc, proc, {}, proc, args)
        return result

    def __aiter__(self):
        return self.__wrapped__.__aiter__()


class AIOTracedConnection(wrapt.ObjectProxy):
    """TracedConnection wraps a Connection with tracing code."""

    def __init__(self, conn, db_tags, cursor_cls=AIOTracedCursor):
        super(AIOTracedConnection, self).__init__(conn)
        # wrapt requires prefix of `_self` for attributes that are only in the
        # proxy (since some of our source objects will use `__slots__`)
        self._self_cursor_cls = cursor_cls
        self._self_db_tags = db_tags

    # unfortunately we also need to patch this method as otherwise "self"
    # ends up being the aiopg connection object
    if AIOPG_VERSION >= (0, 16, 0):

        def cursor(self, *args, **kwargs):
            # Only one cursor per connection is allowed, as per DB API spec
            self.close_cursor()
            self._last_usage = self._loop.time()

            coro = self._cursor(*args, **kwargs)
            return _ContextManager(coro)

    else:

        def cursor(self, *args, **kwargs):
            coro = self._cursor(*args, **kwargs)
            return _ContextManager(coro)

    async def _cursor(self, *args, **kwargs):
        cursor = await self.__wrapped__._cursor(*args, **kwargs)
        return self._self_cursor_cls(cursor, self._self_db_tags)
