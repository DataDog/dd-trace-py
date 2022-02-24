import pymysql.connections

from ...vendor.debtcollector.removals import remove


@remove(message="Use patching instead (see the docs).", removal_version="1.0.0")
def get_traced_pymysql_connection(*args, **kwargs):
    return pymysql.connections.Connection
