"""Instrument sqlite3 to report SQLite queries.

Patch your sqlite3 connection to make it work.

    from ddtrace import Pin, patch
    import sqlite3

    # Instrument the sqlite3 library
    patch(sqlite3=True)

    # This will report a span with the default settings
    db = sqlite3.connect(":memory:")
    cursor = db.cursor()
    cursor.execute("select * from users where id = 1")

    # To customize one client
    Pin.get_from(db).service = 'my-sqlite'
"""
from .connection import connection_factory
from .patch import patch

__all__ = ['connection_factory', 'patch']
