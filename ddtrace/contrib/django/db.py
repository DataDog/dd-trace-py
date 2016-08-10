
import logging

from django.db import connections

# project
from ...ext import sql as sqlx
from ...ext import AppTypes


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

        self._vendor = getattr(conn, 'vendor', 'db')     # e.g sqlite, postgres
        self._alias = getattr(conn, 'alias', 'default')  # e.g. default, users

        prefix = sqlx.normalize_vendor(self._vendor)
        self._name = "%s.%s" % (prefix, "query")                # e.g sqlite.query
        self._service = "%s%s" % (self._alias or prefix, "db")  # e.g. defaultdb or postgresdb

        self.tracer.set_service_info(
            service=self._service,
            app=prefix,
            app_type=AppTypes.db,
        )

    def _trace(self, func, sql, params):
        span = self.tracer.trace(self._name,
            resource=sql,
            service=self._service,
            span_type=sqlx.TYPE)

        with span:
            span.set_tag(sqlx.QUERY, sql)
            span.set_tag("django.db.vendor", self._vendor)
            span.set_tag("django.db.alias", self._alias)
            try:
                return func(sql, params)
            finally:
                rows = self.cursor.cursor.rowcount
                if rows and 0 <= rows:
                    span.set_tag(sqlx.ROWS, self.cursor.cursor.rowcount)

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
