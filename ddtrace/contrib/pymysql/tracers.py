import pymysql.connections

from ddtrace.util import deprecated

@deprecated(message='Use patching instead (see the docs).', version='0.6.0')
def get_traced_pymysql_connection(*args, **kwargs):
    return pymysql.connections.Connection
