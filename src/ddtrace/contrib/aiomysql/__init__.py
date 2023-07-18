"""
The aiomysql integration instruments the aiomysql library to trace MySQL queries.

Enabling
~~~~~~~~

The integration is enabled automatically when using
:ref:`ddtrace-run<ddtracerun>` or :func:`patch_all()<ddtrace.patch_all>`.

Or use :func:`patch()<ddtrace.patch>` to manually enable the integration::

    from ddtrace import patch
    patch(aiomysql=True)


Instance Configuration
~~~~~~~~~~~~~~~~~~~~~~

To configure the integration on an per-connection basis use the
``Pin`` API::

    from ddtrace import Pin
    import asyncio
    import aiomysql

    # This will report a span with the default settings
    conn = await aiomysql.connect(host="127.0.0.1", port=3306,
                                  user="root", password="", db="mysql",
                                  loop=loop)

    # Use a pin to override the service name for this connection.
    Pin.override(conn, service="mysql-users")


    cur = await conn.cursor()
    await cur.execute("SELECT 6*7 AS the_answer;")
"""

from ...internal.utils.importlib import require_modules


required_modules = ["aiomysql"]

with require_modules(required_modules) as missing_modules:
    if not missing_modules:
        from .patch import patch
        from .patch import unpatch

        __all__ = ["patch", "unpatch"]
