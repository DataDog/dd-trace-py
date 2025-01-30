"""
Instrument aiopg to report a span for each executed Postgres queries::

    from ddtrace import patch
    from ddtrace.trace import Pin
    import aiopg

    # If not patched yet, you can patch aiopg specifically
    patch(aiopg=True)

    # This will report a span with the default settings
    async with aiopg.connect(DSN) as db:
        with (await db.cursor()) as cursor:
            await cursor.execute("SELECT * FROM users WHERE id = 1")

    # Use a pin to specify metadata related to this connection
    Pin.override(db, service='postgres-users')
"""


# Required to allow users to import from  `ddtrace.contrib.aiohttp.patch` directly
import warnings as _w


with _w.catch_warnings():
    _w.simplefilter("ignore", DeprecationWarning)
    from . import patch as _  # noqa: F401, I001

from ddtrace.contrib.internal.aiopg.patch import get_version  # noqa: F401
from ddtrace.contrib.internal.aiopg.patch import patch  # noqa: F401
