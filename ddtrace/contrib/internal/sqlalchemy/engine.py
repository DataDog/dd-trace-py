"""
To trace sqlalchemy queries, add instrumentation to the engine class or
instance you are using::

    from ddtrace.trace import tracer
    from ddtrace.contrib.sqlalchemy import trace_engine
    from sqlalchemy import create_engine

    engine = create_engine('sqlite:///:memory:')
    trace_engine(engine, tracer, 'my-database')

    engine.connect().execute('select count(*) from users')
"""
# 3p
import sqlalchemy
from sqlalchemy.event import listen

# project
import ddtrace
from ddtrace import config
from ddtrace.constants import _SPAN_MEASURED_KEY
from ddtrace.constants import SPAN_KIND
from ddtrace.ext import SpanKind
from ddtrace.ext import SpanTypes
from ddtrace.ext import db
from ddtrace.ext import net as netx
from ddtrace.ext import sql as sqlx
from ddtrace.internal.constants import COMPONENT
from ddtrace.internal.schema import schematize_database_operation
from ddtrace.internal.schema import schematize_service_name
from ddtrace.trace import Pin


def trace_engine(engine, tracer=None, service=None):
    """
    Add tracing instrumentation to the given sqlalchemy engine or instance.

    :param sqlalchemy.Engine engine: a SQLAlchemy engine class or instance
    :param ddtrace.trace.Tracer tracer: a tracer instance. will default to the global
    :param str service: the name of the service to trace.
    """
    tracer = tracer or ddtrace.tracer  # by default use global
    EngineTracer(tracer, service, engine)


def _wrap_create_engine(func, module, args, kwargs):
    """Trace the SQLAlchemy engine, creating an `EngineTracer`
    object that will listen to SQLAlchemy events. A PIN object
    is attached to the engine instance so that it can be
    used later.
    """
    # the service name is set to `None` so that the engine
    # name is used by default; users can update this setting
    # using the PIN object
    engine = func(*args, **kwargs)
    EngineTracer(ddtrace.tracer, None, engine)
    return engine


class EngineTracer(object):
    def __init__(self, tracer, service, engine):
        self.tracer = tracer
        self.engine = engine
        self.vendor = sqlx.normalize_vendor(engine.name)
        self.service = schematize_service_name(service or self.vendor)
        self.name = schematize_database_operation("%s.query" % self.vendor, database_provider=self.vendor)

        # attach the PIN
        pin = Pin(service=self.service)
        pin._tracer = self.tracer
        pin.onto(engine)

        listen(engine, "before_cursor_execute", self._before_cur_exec)
        listen(engine, "after_cursor_execute", self._after_cur_exec)

        # Determine name of error event to listen for
        # Ref: https://github.com/DataDog/dd-trace-py/issues/841
        if sqlalchemy.__version__[0] != "0":
            error_event = "handle_error"
        else:
            error_event = "dbapi_error"
        listen(engine, error_event, self._handle_db_error)

    def _before_cur_exec(self, conn, cursor, statement, *args):
        pin = Pin.get_from(self.engine)
        if not pin or not pin.enabled():
            # don't trace the execution
            return

        span = pin.tracer.trace(
            self.name,
            service=pin.service,
            span_type=SpanTypes.SQL,
            resource=statement,
        )
        span.set_tag_str(COMPONENT, config.sqlalchemy.integration_name)

        # set span.kind to the type of operation being performed
        span.set_tag_str(SPAN_KIND, SpanKind.CLIENT)

        span.set_tag(_SPAN_MEASURED_KEY)

        if not _set_tags_from_url(span, conn.engine.url):
            _set_tags_from_cursor(span, self.vendor, cursor)

    def _after_cur_exec(self, conn, cursor, statement, *args):
        pin = Pin.get_from(self.engine)
        if not pin or not pin.enabled():
            # don't trace the execution
            return

        span = pin.tracer.current_span()
        if not span:
            return

        try:
            if cursor and cursor.rowcount >= 0:
                span.set_tag(db.ROWCOUNT, cursor.rowcount)
        finally:
            span.finish()

    def _handle_db_error(self, *args):
        pin = Pin.get_from(self.engine)
        if not pin or not pin.enabled():
            # don't trace the execution
            return

        span = pin.tracer.current_span()
        if not span:
            return

        try:
            span.set_traceback()
        finally:
            span.finish()


def _set_tags_from_url(span, url):
    """set connection tags from the url. return true if successful."""
    if url.host:
        span.set_tag_str(netx.TARGET_HOST, url.host)
        span.set_tag_str(netx.SERVER_ADDRESS, url.host)
    if url.port:
        span.set_tag(netx.TARGET_PORT, url.port)
    if url.database:
        span.set_tag_str(sqlx.DB, url.database)

    return bool(span.get_tag(netx.TARGET_HOST))


def _set_tags_from_cursor(span, vendor, cursor):
    """attempt to set db connection tags by introspecting the cursor."""
    if "postgres" == vendor:
        if hasattr(cursor, "connection"):
            dsn = getattr(cursor.connection, "dsn", None)
            if dsn:
                d = sqlx.parse_pg_dsn(dsn)
                span.set_tag_str(sqlx.DB, d.get("dbname"))
                span.set_tag_str(netx.TARGET_HOST, d.get("host"))
                span.set_tag_str(netx.SERVER_ADDRESS, d.get("host"))
                span.set_metric(netx.TARGET_PORT, int(d.get("port")))
