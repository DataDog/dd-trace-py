"""
Instrument aiopg to report a span for each executed Postgres queries::

    from ddtrace import patch
    import aiopg

    # If not patched yet, you can patch aiopg specifically
    patch(aiopg=True)

    # This will report a span with the default settings
    async with aiopg.connect(DSN) as db:
        with (await db.cursor()) as cursor:
            await cursor.execute("SELECT * FROM users WHERE id = 1")

Configuration
~~~~~~~~~~~~~

.. py:data:: ddtrace.config.aiopg["service"]

   The service name reported by default for aiopg spans.

   This option can also be set with the ``DD_AIOPG_SERVICE`` environment
   variable.

   Default: ``"postgres"``
"""
