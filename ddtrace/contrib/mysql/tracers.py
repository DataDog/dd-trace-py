"""
tracers exposed publicly
"""
# stdlib
import time

# dogtrace
from ...ext import sql as sqlx
from ...ext import AppTypes


DEFAULT_SERVICE = 'mysql'


def get_traced_mysql(ddtracer, service=DEFAULT_SERVICE, meta=None):
    return _get_traced_mysql(ddtracer, mysql.connector, service, meta)


def get_traced_mysql_from(ddtracer, baseclass, service=DEFAULT_SERVICE, meta=None):
    return _get_traced_mysql(ddtracer, baseclass, service, meta)

# pylint: disable=protected-access
def _get_traced_mysql(ddtracer, baseclass, service, meta):
    ddtracer.set_service_info(
        service=service,
        app="mysql",
        app_type=AppTypes.db,
    )

    class TracedMysql(baseclass):
        _datadog_tracer = ddtracer
        _datadog_service = service
        _datadog_meta = meta

        @classmethod
        def set_datadog_meta(cls, meta):
            cls._datadog_meta = meta

        def connect(self, *args, **kwargs):
            with self._datadog_tracer.trace('mysql.connect') as s:
                if s.sampled:
                    s.service = self._datadog_service
                    s.span_type = sqlx.TYPE

                    # query = format_command_args(args)
                    # s.resource = query
                    # non quantized version
                    #s.set_tag(sqlx.RAWCMD, query)

                    #s.set_tags(_extract_conn_tags(self.connection_pool.connection_kwargs))
                    s.set_tags(self._datadog_meta)
                    #s.set_metric(sqlx.ARGS_LEN, len(args))

                return super(TracedMysql, self).execute_command(*args, **options)

    return TracedMysql
