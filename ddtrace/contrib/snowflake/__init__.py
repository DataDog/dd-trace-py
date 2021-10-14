"""
The snowflake integration instruments the ``snowflake-connector-python`` library to trace Snowflake queries.

Enabling
~~~~~~~~

The integration is enabled automatically when using
:ref:`ddtrace-run<ddtracerun>` or :ref:`patch_all()<patch_all>`.

Or use :ref:`patch()<patch>` to manually enable the integration::

    from ddtrace import patch
    patch(snowflake=True)


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

    from ddtrace import Pin
    from snowflake.connector import connect

    # This will report a span with the default settings
    conn = connect(user="alice", password="b0b", account="dev")

    # Use a pin to override the service name for this connection.
    Pin.override(conn, service="snowflake-dev")


    cursor = conn.cursor()
    cursor.execute("SELECT current_version()")
"""
from ...utils.importlib import require_modules


required_modules = ["snowflake.connector"]

with require_modules(required_modules) as missing_modules:
    if not missing_modules:
        from .patch import patch
        from .patch import unpatch

        __all__ = ["patch", "unpatch"]
