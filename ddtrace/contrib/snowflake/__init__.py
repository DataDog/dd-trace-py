"""
The snowflake integration instruments the ``snowflake-connector-python`` library to trace Snowflake queries.

Note that this integration is in beta.

Enabling
~~~~~~~~

The integration is not enabled automatically when using
:ref:`ddtrace-run<ddtracerun>` or :ref:`import ddtrace.auto<ddtraceauto>`.

Use ``DD_TRACE_SNOWFLAKE_ENABLED=true`` to enable it with ``ddtrace-run``

or :func:`patch()<ddtrace.patch>` to manually enable the integration::

    from ddtrace import patch
    patch(snowflake=True)

or use :func:`patch_all()<ddtrace.patch_all>` to manually enable the integration::

    from ddtrace import patch_all
    patch_all(snowflake=True)




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


# Required to allow users to import from  `ddtrace.contrib.snowflake.patch` directly
import warnings as _w


with _w.catch_warnings():
    _w.simplefilter("ignore", DeprecationWarning)
    from . import patch as _  # noqa: F401, I001


from ddtrace.contrib.internal.snowflake.patch import get_version  # noqa: F401
from ddtrace.contrib.internal.snowflake.patch import patch  # noqa: F401
from ddtrace.contrib.internal.snowflake.patch import unpatch  # noqa: F401
