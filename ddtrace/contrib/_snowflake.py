"""
The snowflake integration instruments the ``snowflake-connector-python`` library to trace Snowflake queries.

Note that this integration is in beta.

Enabling
~~~~~~~~

The integration is not enabled automatically when using
:ref:`ddtrace-run<ddtracerun>` or :ref:`import ddtrace.auto<ddtraceauto>`.

Use environment variable ``DD_TRACE_SNOWFLAKE_ENABLED=true`` or `DD_PATCH_MODULES:snowflake:true`
to manually enable the integration.


Global Configuration
~~~~~~~~~~~~~~~~~~~~

.. py:data:: ddtrace.config.snowflake["service"]

   The service name reported by default for snowflake spans.

   This option can also be set with the ``DD_SNOWFLAKE_SERVICE`` environment
   variable.

   Default: ``"snowflake"``

.. py:data:: ddtrace.config.snowflake["trace_fetch_methods"]

   Whether or not to trace fetch methods.

   Can also configured via the ``DD_SNOWFLAKE_TRACE_FETCH_METHODS`` environment variable.

   Default: ``False``


Instance Configuration
~~~~~~~~~~~~~~~~~~~~~~

To configure the integration on an per-connection basis use the
``Pin`` API::

    from ddtrace.trace import Pin
    from snowflake.connector import connect

    # This will report a span with the default settings
    conn = connect(user="alice", password="b0b", account="dev")

    # Use a pin to override the service name for this connection.
    Pin.override(conn, service="snowflake-dev")


    cursor = conn.cursor()
    cursor.execute("SELECT current_version()")
"""
