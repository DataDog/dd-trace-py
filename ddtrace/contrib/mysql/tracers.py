"""
tracers exposed publicly
"""
# stdlib
import time

from mysql.connector.connection import MySQLConnection
from mysql.connector.cursor import MySQLCursor
from mysql.connector.cursor import MySQLCursorRaw
from mysql.connector.cursor import MySQLCursorBuffered
from mysql.connector.cursor import MySQLCursorBufferedRaw
from mysql.connector.errors import NotSupportedError
from mysql.connector.errors import ProgrammingError

# dogtrace
from ...ext import net
from ...ext import db
from ...ext import sql as sqlx
from ...ext import AppTypes


DEFAULT_SERVICE = 'mysql'

def get_traced_mysql_connection(ddtracer, service=DEFAULT_SERVICE, meta=None):
    return _get_traced_mysql(ddtracer, MySQLConnection, service, meta)

# # _mysql_connector unsupported for now, main reason being:
# # not widespread yet, not easily instalable on our test envs.
# # Once this is fixed, no reason not to support it.
# def get_traced_mysql_connection_from(ddtracer, baseclass, service=DEFAULT_SERVICE, meta=None):
#    return _get_traced_mysql(ddtracer, baseclass, service, meta)

# pylint: disable=protected-access
def _get_traced_mysql(ddtracer, connection_baseclass, service, meta):
    ddtracer.set_service_info(
        service=service,
        app='mysql',
        app_type=AppTypes.db,
    )

    class TracedMySQLConnection(connection_baseclass):
        _datadog_tracer = ddtracer
        _datadog_service = service
        _datadog_meta = meta

        @classmethod
        def set_datadog_meta(cls, meta):
            cls._datadog_meta = meta

        def __init__(self, *args, **kwargs):
            self._datadog_connection_creation = time.time()
            super(TracedMySQLConnection, self).__init__(*args, **kwargs)
            self._datadog_tags = {}
            for v in ((net.TARGET_HOST, "host"),
                      (net.TARGET_PORT, "port"),
                      (db.NAME, "database"),
                      (db.USER, "user")):
                if v[1] in kwargs:
                    self._datadog_tags[v[0]] = kwargs[v[1]]

        def cursor(self, buffered=None, raw=None, cursor_class=None):
            db = self

            # using MySQLCursor* constructors instead of super cursor
            # method as this one does not give a direct access to the
            # class makes overriding tricky
            if cursor_class:
                cursor_baseclass = cursor_class
            else:
                if raw:
                    if buffered:
                        cursor_baseclass = MySQLCursorBufferedRaw
                    else:
                        cursor_baseclass = MySQLCursorRaw
                else:
                    if buffered:
                        cursor_baseclass = MySQLCursorBuffered
                    else:
                        cursor_baseclass = MySQLCursor

            class TracedMySQLCursor(cursor_baseclass):
                _datadog_tracer = ddtracer
                _datadog_service = service
                _datadog_meta = meta

                @classmethod
                def set_datadog_meta(cls, meta):
                    cls._datadog_meta = meta

                def __init__(self, db=None):
                    if db is None:
                        raise NotSupportedError(
                            "db is None, "
                            "it should be defined before cursor "
                            "creation when using ddtrace, "
                            "please check your connection param")
                    if not hasattr(db, "_datadog_tags"):
                        raise ProgrammingError(
                            "TracedMySQLCursor should be initialized"
                            "with a TracedMySQLConnection")
                    self._datadog_tags = db._datadog_tags
                    self._datadog_cursor_creation = time.time()
                    super(TracedMySQLCursor, self).__init__(db)

                def execute(self, *args, **kwargs):
                    with self._datadog_tracer.trace('mysql.execute') as s:
                        if s.sampled:
                            s.service = self._datadog_service
                            s.span_type = sqlx.TYPE
                            if len(args) >= 1:
                                operation = args[0]
                            if "operation" in kwargs:
                                operation = kwargs["operation"]
                            s.resource = operation
                            s.set_tag(sqlx.QUERY, operation)
                            s.set_tag(sqlx.DB, 'mysql')
                            s.set_tags(self._datadog_tags)
                            s.set_tags(self._datadog_meta)
                            result = super(TracedMySQLCursor, self).execute(*args, **kwargs)
                            s.set_metric(sqlx.ROWS, self.rowcount)
                            return result

                        return super(TracedMySQLCursor, self).execute(*args, **kwargs)

            return TracedMySQLCursor(db=db)

    return TracedMySQLConnection
