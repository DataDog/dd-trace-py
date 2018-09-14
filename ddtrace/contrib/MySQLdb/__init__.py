"""Instrumeent mysql to report MySQL queries.

``patch_all`` will automatically patch your mysql connection to make it work.
::

    from ddtrace import Pin, patch
    import MySQLdb

    # If not patched yet, you can patch mysql specifically
    patch(MySQLdb=True)

    # This will report a span with the default settings
    conn = MySQLdb.connect(user="alice", password="b0b", host="localhost", port=3306, database="test")
    cursor = conn.cursor()
    cursor.execute("SELECT 6*7 AS the_answer;")

    # Use a pin to specify metadata related to this connection
    Pin.override(conn, service='mysql-users')

Help on MySQL-python can be found on:
https://sourceforge.net/projects/mysql-python/
"""
import logging

from ..util import require_modules


log = logging.getLogger(__name__)

# check `MySQL-python` availability
required_modules = ['_mysql']

with require_modules(required_modules) as missing_modules:
    if not missing_modules:
        # MySQL-python package is not supported at the moment
        log.debug('failed to patch MySQLdb: integration not available')

# check `mysql-connector` availability
required_modules = ['MySQLdb']

with require_modules(required_modules) as missing_modules:
    if not missing_modules:
        from .patch import patch

        __all__ = ['patch']
