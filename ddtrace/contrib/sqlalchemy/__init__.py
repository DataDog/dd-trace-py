

# 3p
import sqlalchemy
from sqlalchemy.event import listen

# project
import ddtrace
from ddtrace.ext import sql as sqlx
from ddtrace.ext import errors as errorsx


def trace_engine(engine, tracer=None, service=None):

    tracer = tracer or ddtrace.tracer # by default use the global tracing instance.

    EngineTracer(tracer, service, engine)


class EngineTracer(object):

    def __init__(self, tracer, service, engine):
        self.tracer = tracer
        self.service = service
        self.engine = engine
        self.vendor = engine.name or "db"

        self.span = None

        listen(engine, 'before_cursor_execute', self._before_cur_exec)
        listen(engine, 'after_cursor_execute', self._after_cur_exec)
        listen(engine, 'dbapi_error', self._dbapi_error)

    def _before_cur_exec(self, conn, cursor, statement, parameters, context, executemany):
        self.span = None
        self.span = self.tracer.trace("foo", span_type=sqlx.TYPE)
        self.span.resource = statement
        self.span.set_tag(sqlx.QUERY, statement)

    def _after_cur_exec(self, conn, cursor, statement, parameters, context, executemany):
        span = self._pop_span()
        if not span:
            return

        try:
            if cursor and cursor.rowcount >= 0:
                span.set_tag(sqlx.ROWS, cursor.rowcount)
        finally:
            span.finish()

    def _dbapi_error(self, conn, cursor, statement, parameters, context, exception):
        span = self._pop_span()
        if not span:
            return

        try:
            span.set_traceback()
        finally:
            span.finish()

    def _pop_span(self):
        span = self.span
        self.span = None
        return span

