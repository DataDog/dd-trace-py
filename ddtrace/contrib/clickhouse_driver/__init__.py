"""
The clickhouse_driver integration instruments the clickhouse-driver library to trace ClickHouse queries.


Enabling
~~~~~~~~

The clickhouse_driver integration is enabled automatically when using
:ref:`ddtrace-run<ddtracerun>` or :func:`patch_all()<ddtrace.patch_all>`.

Or use :func:`patch()<ddtrace.patch>` to manually enable the integration::

    from ddtrace import patch
    patch(clickhouse_driver=True)


Global Configuration
~~~~~~~~~~~~~~~~~~~~

.. py:data:: ddtrace.config.clickhouse_driver["service"]

   The service name reported by default for clickhouse spans.

   This option can also be set with the ``DD_CLICKHOUSE_SERVICE`` environment
   variable.

   Default: ``"clickhouse"``

.. py:data:: ddtrace.config.clickhouse_driver["trace_fetch_methods"]

   Whether or not to trace fetch methods.

   Can also configured via the ``DD_CLICKHOUSE_TRACE_FETCH_METHODS`` environment variable.

   Default: ``False``


Instance Configuration
~~~~~~~~~~~~~~~~~~~~~~

To configure the clickhouse-driver integration on an per-connection basis use the
``Pin`` API::

    from ddtrace import Pin
    # Make sure to import clickhouse_driver.dbapi and not the 'connect' function,
    # otherwise you won't have access to the patched version
    import clickhouse_driver.dbapi

    # This will report a span with the default settings
    conn = clickhouse_driver.dbapi.connect(user="alice", password="b0b", host="localhost", port=9000, database="test")

    # Use a pin to override the service name for this connection.
    Pin.override(conn, service='clickhouse-users')

    cursor = conn.cursor()
    cursor.execute("SELECT 6*7 AS the_answer;")

Help on clickhouse_driver.dbapi can be found on:
https://clickhouse-driver.readthedocs.io/en/latest/dbapi.html
"""

from ddtrace.internal.utils.importlib import require_modules

# check `clickhouse-driver` availability
required_modules = ["clickhouse-driver"]

with require_modules(required_modules) as missing_modules:
    if not missing_modules:
        from .patch import patch

        __all__ = ["patch"]
