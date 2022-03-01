import mysql.connector

from ddtrace.internal.utils.deprecations import DDTraceDeprecationWarning

from ...vendor.debtcollector.removals import remove


@remove(message="Use patching instead (see the docs).", category=DDTraceDeprecationWarning, removal_version="1.0.0")
def get_traced_mysql_connection(*args, **kwargs):
    return mysql.connector.MySQLConnection
