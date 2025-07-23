"""
The aiomysql integration instruments the aiomysql library to trace MySQL queries.

Enabling
~~~~~~~~

The integration is enabled automatically when using
:ref:`ddtrace-run<ddtracerun>` or :ref:`import ddtrace.auto<ddtraceauto>`.

Or use :func:`patch()<ddtrace.patch>` to manually enable the integration::

    from ddtrace import patch
    patch(aiomysql=True)


Instance Configuration
~~~~~~~~~~~~~~~~~~~~~~

To configure the integration on an per-connection basis use the
``Pin`` API::

    from ddtrace.trace import Pin
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
