"""Instrument aiopg to report Postgres queries.

``patch`` will automatically patch your aiopg connection to make it work.
::

    from ddtrace import Pin, patch
    import aiopg

    # If not patched yet, you can patch aiopg specifically
    patch(aiopg=True)

    # This will report a span with the default settings
    async with aiopg.connect(connection_factory=factory) as db:
        with (await db.cursor()) as cursor:
            await cursor.execute("select * from users where id = 1")

    # Use a pin to specify metadata related to this connection
    Pin.override(db, service='postgres-users')
"""
from ..util import require_modules

required_modules = ['aiopg']

with require_modules(required_modules) as missing_modules:
    if not missing_modules:
        from .patch import patch

        __all__ = ['patch']
