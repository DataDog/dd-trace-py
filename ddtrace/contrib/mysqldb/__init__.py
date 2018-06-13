"""Instrument mysqlclient / MySQL-python to report MySQL queries.

``patch_all`` will automatically patch your mysql connection to make it work.
::

    from ddtrace import Pin, patch
    from MySQLdb import connect

    # If not patched yet, you can patch mysqldb specifically
    patch(mysqldb=True)

    # This will report a span with the default settings
    conn = connect(user="alice", passwd="b0b", host="localhost", port=3306, db="test")
    cursor = conn.cursor()
    cursor.execute("SELECT 6*7 AS the_answer;")

    # Use a pin to specify metadata related to this connection
    Pin.override(conn, service='mysql-users')

This package works for mysqlclient or MySQL-python
Only the default full-Python integration works. The binary C connector,
provided by _mysql, is not supported yet.

Help on mysqlclient can be found on:
https://mysqlclient.readthedocs.io/
"""
from ...utils.importlib import require_modules

required_modules = ['MySQLdb']

with require_modules(required_modules) as missing_modules:
    if not missing_modules:
        from .patch import patch

        __all__ = ['patch']
