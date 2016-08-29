"""
tracers exposed publicly
"""
# stdlib
import time
import copy

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
_TRACEABLE_EXECUTE_FUNCS = ["execute",
                            "executemany"]
_TRACEABLE_FETCH_FUNCS = ["fetchall",
                          "fetchone",
                          "fetchmany",
                          "fetchwarnings"]
_TRACEABLE_FUNCS = copy.deepcopy(_TRACEABLE_EXECUTE_FUNCS)
_TRACEABLE_FUNCS.extend(_TRACEABLE_FETCH_FUNCS)

def get_traced_mysql_connection(ddtracer, service=DEFAULT_SERVICE, meta=None, trace_fetch=False):
    """Return a class which can be used to instanciante MySQL connections.

    Keyword arguments:
    ddtracer -- the tracer to use
    service -- the service name
    meta -- your custom meta data
    trace_fetch -- set to True if you want fetchall, fetchone,
        fetchmany and fetchwarnings to be traced. By default
        only execute and executemany are traced.
    """
    if trace_fetch:
        traced_funcs = _TRACEABLE_FUNCS
    else:
        traced_funcs = _TRACEABLE_EXECUTE_FUNCS
    return _get_traced_mysql_connection(ddtracer, MySQLConnection, service, meta, traced_funcs)

# # _mysql_connector unsupported for now, main reason being:
# # not widespread yet, not easily instalable on our test envs.
# # Once this is fixed, no reason not to support it.
# def get_traced_mysql_connection_from(ddtracer, baseclass, service=DEFAULT_SERVICE, meta=None):
#    return _get_traced_mysql_connection(ddtracer, baseclass, service, meta, traced_funcs)

# pylint: disable=protected-access
def _get_traced_mysql_connection(ddtracer, connection_baseclass, service, meta, traced_funcs):
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

        def set_datadog_traced_funcs(self, traced_funcs):
            self._datadog_traced_funcs = traced_funcs

        def __init__(self, *args, **kwargs):
            self._datadog_connection_creation = time.time()
            self.set_datadog_traced_funcs(traced_funcs)
            super(TracedMySQLConnection, self).__init__(*args, **kwargs)
            self._datadog_tags = {}
            for v in ((net.TARGET_HOST, "host"),
                      (net.TARGET_PORT, "port"),
                      (db.NAME, "database"),
                      (db.USER, "user")):
                if v[1] in kwargs:
                    self._datadog_tags[v[0]] = kwargs[v[1]]
            self._datadog_cursor_kwargs = {}
            for v in ("buffered", "raw"):
                if v in kwargs:
                    self._datadog_cursor_kwargs[v] = kwargs[v]

        def cursor(self, buffered=None, raw=None, cursor_class=None):
            db = self

            if "buffered" in db._datadog_cursor_kwargs and db._datadog_cursor_kwargs["buffered"]:
                buffered = True
            if "raw" in db._datadog_cursor_kwargs and db._datadog_cursor_kwargs["raw"]:
                raw = True
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
                    self._datadog_baseclass_name = cursor_baseclass.__name__
                    super(TracedMySQLCursor, self).__init__(db)

                # using *args, **kwargs instead of "operation, params, multi"
                # as multi, typically, might be available or not depending
                # on the version of mysql.connector
                def _datadog_execute(self, dd_func_name, *args, **kwargs):
                    super_func = getattr(super(TracedMySQLCursor, self),dd_func_name)
                    if len(args) >= 1:
                        operation = args[0]
                    if "operation" in kwargs:
                        operation = kwargs["operation"]
                    # keep it for fetch* methods
                    self._datadog_operation = operation
                    if dd_func_name in db._datadog_traced_funcs:
                        with self._datadog_tracer.trace('mysql.' + dd_func_name) as s:
                            if s.sampled:
                                s.service = self._datadog_service
                                s.span_type = sqlx.TYPE
                                s.resource = operation
                                s.set_tag(sqlx.QUERY, operation)
                                s.set_tag(sqlx.DB, 'mysql')
                                s.set_tags(self._datadog_tags)
                                s.set_tags(self._datadog_meta)
                                result = super_func(*args,**kwargs)
                                # Note, as stated on
                                # https://dev.mysql.com/doc/connector-python/en/connector-python-api-mysqlcursor-rowcount.html
                                # rowcount is not known before rows are fetched,
                                # unless the cursor is a buffered one.
                                # Don't be surprised if it's "-1"
                                s.set_metric(sqlx.ROWS, self.rowcount)
                                return result
                            # not sampled
                            return super_func(*args, **kwargs)
                    else:
                        # not using traces on this callback
                        return super_func(*args, **kwargs)

                def execute(self, *args, **kwargs):
                    return self._datadog_execute('execute', *args, **kwargs)

                def executemany(self, *args, **kwargs):
                    return self._datadog_execute('executemany', *args, **kwargs)

                def _datadog_fetch(self, dd_func_name, *args, **kwargs):
                    super_func = getattr(super(TracedMySQLCursor, self),dd_func_name)
                    if dd_func_name in db._datadog_traced_funcs:
                        with self._datadog_tracer.trace('mysql.' + dd_func_name) as s:
                            if s.sampled:
                                s.service = self._datadog_service
                                s.span_type = sqlx.TYPE
                                # _datadog_operation refers to last execute* call
                                if hasattr(self,"_datadog_operation"):
                                    s.resource = self._datadog_operation
                                    s.set_tag(sqlx.QUERY, self._datadog_operation)
                                s.set_tag(sqlx.DB, 'mysql')
                                s.set_tags(self._datadog_tags)
                                s.set_tags(self._datadog_meta)
                                result = super_func(*args, **kwargs)
                                s.set_metric(sqlx.ROWS, self.rowcount)
                                return result
                            # not sampled
                            return super_func(*args, **kwargs)
                    else:
                        # not using traces on this callback
                        return super_func(*args, **kwargs)

                def fetchall(self, *args, **kwargs):
                    return self._datadog_fetch('fetchall', *args, **kwargs)

                def fetchmany(self, *args, **kwargs):
                    return self._datadog_fetch('fetchmany', *args, **kwargs)

                def fetchone(self, *args, **kwargs):
                    return self._datadog_fetch('fetchone', *args, **kwargs)

                def fetchwarnings(self, *args, **kwargs):
                    return self._datadog_fetch('fetchwarnings', *args, **kwargs)

            return TracedMySQLCursor(db=db)

    return TracedMySQLConnection
