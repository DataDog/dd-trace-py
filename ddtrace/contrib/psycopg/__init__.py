"""Instrument psycopg2 to report Postgres queries.

Patch your psycopg2 connection to make it work.

    from ddtrace import Pin, patch
    import psycopg2
    patch(psycopg=True)

    # This will report a span with the default settings
    db = psycopg2.connect(connection_factory=factory)
    cursor = db.cursor()
    cursor.execute("select * from users where id = 1")

    # To customize one client
    Pin.get_from(db).service = 'my-postgres'
"""
from ..util import require_modules

required_modules = ['psycopg2']

with require_modules(required_modules) as missing_modules:
    if not missing_modules:
        from .connection import connection_factory
        from .patch import patch, patch_conn

        __all__ = ['connection_factory', 'patch', 'patch_conn']
