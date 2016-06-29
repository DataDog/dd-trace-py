
import logging

from django.db import connections

# project
from ...ext import sql as sqlx


log = logging.getLogger(__name__)


def patch_db(tracer):
    for c in connections.all():
        patch_conn(tracer, c)

def patch_conn(tracer, conn):
    attr = '_datadog_original_cursor'

    if hasattr(conn, attr):
        log.debug("already patched")
        return

    conn._datadog_original_cursor = conn.cursor
    def cursor():
        return TracedCursor(tracer, conn, conn._datadog_original_cursor())
    conn.cursor = cursor


class TracedCursor(object):

    def __init__(self, tracer, conn, cursor):
        self.tracer = tracer
        self.conn = conn
        self.cursor = cursor

        self._prefix = conn.vendor or "db"                              # e.g sqlite, postgres, etc.
        self._name = "%s.%s" % (self._prefix, "query")                  # e.g sqlite.query
        self._service = "%s%s" % (conn.alias or self._prefix, "db")     # e.g. defaultdb or postgresdb

    def _trace(self, func, sql, params):
        with self.tracer.trace(self._name, service=self._service, span_type=sqlx.TYPE) as span:
            span.set_tag(sqlx.QUERY, sql)
            return func(sql, params)

    def callproc(self, procname, params=None):
        return self._trace(self.cursor.callproc, procname, params)

    def execute(self, sql, params=None):
        return self._trace(self.cursor.execute, sql, params)

    def executemany(self, sql, param_list):
        return self._trace(self.cursor.executemany, sql, param_list)

    def close(self):
        return self.cursor.close()

    def __getattr__(self, attr):
        return getattr(self.cursor, attr)

    def __iter__(self):
        return iter(self.cursor)

    def __enter__(self):
        return self

    def __exit__(self, type, value, traceback):
        self.close()
