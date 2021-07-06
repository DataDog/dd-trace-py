"""
The  integration instruments the mariadb library to trace mariadb queries.


Enabling
~~~~~~~~

The mariadb integration is enabled automatically when using
:ref:`ddtrace-run<ddtracerun>` or :ref:`patch_all()<patch_all>`.

Or use :ref:`patch()<patch>` to manually enable the integration::

    from ddtrace import patch
    patch(mariadb=True)


Global Configuration
~~~~~~~~~~~~~~~~~~~~

.. py:data:: ddtrace.config.mariadb["service"]

   The service name reported by default for mariadb spans.

   This option can also be set with the ``DD_MARIADB_SERVICE`` environment
   variable.

   Default: ``"mariadb"``


Instance Configuration
~~~~~~~~~~~~~~~~~~~~~~

To configure the mariadb integration on an per-connection basis use the
``Pin`` API::

    from ddtrace import Pin
    # Make sure to import mariadb.connector and not the 'connect' function,
    # otherwise you won't have access to the patched version
    import mariadb.connector

    # This will report a span with the default settings
    conn = mariadb.connector.connect(user="alice", password="b0b", host="localhost", port=3306, database="test")

    # Use a pin to override the service name for this connection.
    Pin.override(conn, service='mariadb-users')

    cursor = conn.cursor()
    cursor.execute("SELECT 6*7 AS the_answer;")


Only the default full-Python integration works. The binary C connector,
provided by _mariadb_connector, is not supported.

Help on mariadb.connector can be found on:
https://mariadb-corporation.github.io/mariadb-connector-python/usage.html
"""
from ...utils.importlib import require_modules


# check `mariadb-connector` availability
required_modules = ["mariadb"]

with require_modules(required_modules) as missing_modules:
    if not missing_modules:
        from .patch import patch
        from .patch import unpatch

        __all__ = ["patch", "unpatch"]
