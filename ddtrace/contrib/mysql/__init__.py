"""Instrumeent mysql to report MySQL queries.

Patch your mysql connection to make it work.

    from ddtrace import Pin, patch
    patch(mysql=True)
    from mysql.connector import connect

    # This will report a span with the default settings
    conn = connect(user="alice", password="b0b", host="localhost", port=3306, database="test")
    cursor = conn.cursor()
    cursor.execute("SELECT 6*7 AS the_answer;")

    # To customize one client instrumentation
    Pin.get_from(conn).service = 'my-mysql'

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
        from .patch import patch
        from .tracers import get_traced_mysql_connection

        __all__ = ['get_traced_mysql_connection', 'patch']
