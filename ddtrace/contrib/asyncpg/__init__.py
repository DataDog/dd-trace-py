"""
Instrument `asyncpg` to report a span for each executed Postgres queries::

    from ddtrace import Pin, patch
    import asyncpg

    # If not patched yet, you can patch asyncpg specifically
    patch(asyncpg=True)

    # This will report a span with the default settings
    db = await asyncpg.connect(DSN)
    values = await db.fetch('''SELECT * FROM mytable''')

    # Use a pin to specify metadata related to this connection
    Pin.override(db, service='postgres-users')
"""
from ...utils.importlib import require_modules


required_modules = ["asyncpg"]

with require_modules(required_modules) as missing_modules:
    if not missing_modules:
        from .patch import patch, unpatch

        __all__ = ["patch", "unpatch"]
