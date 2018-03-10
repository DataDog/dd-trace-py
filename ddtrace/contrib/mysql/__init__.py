"""Instrument mysql to report MySQL queries.

``patch_all`` will automatically patch your mysql connection to make it work.
::

    from ddtrace import Pin, patch
    from mysql.connector import connect

    # If not patched yet, you can patch mysql specifically
    patch(mysql=True)

    # This will report a span with the default settings
    conn = connect(user="alice", password="b0b", host="localhost", port=3306, database="test")
    cursor = conn.cursor()
    cursor.execute("SELECT 6*7 AS the_answer;")

    # Use a pin to specify metadata related to this connection
    Pin.override(conn, service='mysql-users')

This package works for mysql.connector version 2.1.x.
Only the default full-Python integration works. The binary C connector,
provided by _mysql_connector, is not supported yet.

Help on mysql.connector can be found on:
https://dev.mysql.com/doc/connector-python/en/
"""
from ..util import require_modules

# check `mysql-connector` availability
required_modules = ['mysql.connector']

with require_modules(required_modules) as missing_modules:
    if not missing_modules:
        from .patch import patch
        from .tracers import get_traced_mysql_connection

        __all__ = ['get_traced_mysql_connection', 'patch']
