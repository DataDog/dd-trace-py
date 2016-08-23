"""
tracers exposed publicly
"""
# stdlib
import time

from mysql.connector.connection import MySQLConnection

# dogtrace
from ...ext import sql as sqlx
from ...ext import AppTypes


DEFAULT_SERVICE = 'mysql'

def get_traced_mysql_connection(ddtracer, service=DEFAULT_SERVICE, meta=None):
    return _get_traced_mysql(ddtracer, MySQLConnection, service, meta)

# _mysql_connector unsupported for now
# def get_traced_mysql_connection_from(ddtracer, baseclass, service=DEFAULT_SERVICE, meta=None):
#    return _get_traced_mysql(ddtracer, baseclass, service, meta)

# pylint: disable=protected-access
def _get_traced_mysql(ddtracer, baseclass, service, meta):
    ddtracer.set_service_info(
        service=service,
        app='mysql',
        app_type=AppTypes.db,
    )

    class TracedMySQLCursor(baseclass):
        _datadog_tracer = ddtracer
        _datadog_service = service
        _datadog_meta = meta

        @classmethod
        def set_datadog_meta(cls, meta):
            cls._datadog_meta = meta

        def __init__(self, *args, **kwargs):
            self._datadog_cursor_creation = time.time()
            super(TracedMySQLCursor, self).__init__(*args, **kwargs)

        def execute(self, operation, params=None):
            with self._datadog_tracer.trace('mysql.execute') as s:
                if s.sampled:
                    s.service = self._datadog_service
                    s.span_type = sqlx.TYPE

                    # FIXME query = format_command_args(args)
                    s.resource = operation
                    # non quantized version
                    s.set_tag(sqlx.QUERY, operation)
                    s.set_tag(sqlx.DB, 'mysql')
                    result = super(TracedMysql, self).execute(self, operation, params)
                    s.set_tags(self._datadog_meta)
                    s.set_metric(sqlx.ROWS, cursor.rowcount)

                return super(TracedMysql, self).execute(self, operation, params)

    class TracedMySQLConnection(baseclass):
        _datadog_tracer = ddtracer
        _datadog_service = service
        _datadog_meta = meta

        @classmethod
        def set_datadog_meta(cls, meta):
            cls._datadog_meta = meta

        def __init__(self, *args, **kwargs):
            self._datadog_connection_creation = time.time()
            super(TracedMySQLConnection, self).__init__(*args, **kwargs)

        def cursor(self, buffered=None, raw=None, cursor_class=None):
            # todo...
            return

    return TracedMySQLConnection
