"""
The MySQL mysql.connector integration works by creating patched
MySQL connection classes which will trace API calls. For basic usage::

    from ddtrace import tracer
    from ddtrace.contrib.mysql import get_traced_mysql_connection

    # Trace the mysql.connector.connection.MySQLConnection class ...
    MySQL = get_traced_mysql_connection(tracer, service="my-mysql-server")
    conn = MySQL(user="alice", password="b0b", host="localhost", port=3306, database="test")
    cursor = conn.cursor()
    cursor.execute("SELECT 6*7 AS the_answer;")

This package works for mysql.connector version 2.1.x.
Only the default full-Python integration works. The binary C connector,
provided by _mysql_connector, is not supported yet.

Help on mysql.connector can be found on:
https://dev.mysql.com/doc/connector-python/en/
"""

from ..util import require_modules

required_modules = ['mysql.connector']

with require_modules(required_modules) as missing_modules:
    if not missing_modules:
        from .tracers import get_traced_mysql_connection

        __all__ = ['get_traced_mysql_connection']
