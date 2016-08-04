"""


"""

# 3p
import sqlalchemy
from sqlalchemy.event import listen

# project
import ddtrace
from ddtrace.buffer import ThreadLocalSpanBuffer
from ddtrace.ext import sql as sqlx
from ddtrace.ext import errors as errorsx
from ddtrace.ext import net as netx


def trace_engine(engine, tracer=None, service=None):
    """
    Add tracing instrumentation to the given sqlalchemy engine or instance.

    :param sqlalchemy.Engine engine: a SQLAlchemy engine class or instance
    :param ddtrace.Tracer tracer: a tracer instance. will default to the global
    :param str service: the name of the service to trace.
    """
    tracer = tracer or ddtrace.tracer # by default use the global tracing instance.
    EngineTracer(tracer, service, engine)


class EngineTracer(object):

    def __init__(self, tracer, service, engine):
        self.tracer = tracer
        self.engine = engine
        self.vendor = sqlx.normalize_vendor(engine.name)
        self.service = service or self.vendor
        self.name = "%s.query" % self.vendor

        self._span_buffer = ThreadLocalSpanBuffer()

        listen(engine, 'before_cursor_execute', self._before_cur_exec)
        listen(engine, 'after_cursor_execute', self._after_cur_exec)
        listen(engine, 'dbapi_error', self._dbapi_error)

    def _before_cur_exec(self, conn, cursor, statement, parameters, context, executemany):
        self._span_buffer.pop() # should always be empty

        span = self.tracer.trace(
            self.name,
            service=self.service,
            span_type=sqlx.TYPE,
            resource=statement)

        # keep the unnormalized query
        span.set_tag(sqlx.QUERY, statement)

        # set address tags
        url = conn.engine.url
        span.set_tag(sqlx.DB, url.database)
        if url.host and url.port:
            # sqlite has no host and port
            span.set_tag(netx.TARGET_HOST, url.host)
            span.set_tag(netx.TARGET_PORT, url.port)

        self._span_buffer.set(span)

    def _after_cur_exec(self, conn, cursor, statement, parameters, context, executemany):
        span = self._span_buffer.pop()
        if not span:
            return

        try:
            if cursor and cursor.rowcount >= 0:
                span.set_tag(sqlx.ROWS, cursor.rowcount)
        finally:
            span.finish()

    def _dbapi_error(self, conn, cursor, statement, parameters, context, exception):
        span = self._span_buffer.pop()
        if not span:
            return

        try:
            span.set_traceback()
        finally:
            span.finish()

