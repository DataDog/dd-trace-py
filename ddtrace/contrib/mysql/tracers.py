import mysql.connector

from ddtrace.util import deprecated

@deprecated(message='Use patching instead (see the docs).', version='0.6.0')
def get_traced_mysql_connection(*args, **kwargs):
    return mysql.connector.MySQLConnection
