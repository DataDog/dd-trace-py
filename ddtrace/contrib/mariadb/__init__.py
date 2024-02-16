"""
The MariaDB integration instruments the
`MariaDB library <https://mariadb-corporation.github.io/mariadb-connector-python/usage.html>`_ to trace queries.


Enabling
~~~~~~~~

The MariaDB integration is enabled automatically when using
:ref:`ddtrace-run<ddtracerun>` or :ref:`import ddtrace.auto<ddtraceauto>`.

Or use :func:`patch()<ddtrace.patch>` to manually enable the integration::

    from ddtrace import patch
    patch(mariadb=True)


Global Configuration
~~~~~~~~~~~~~~~~~~~~

.. py:data:: ddtrace.config.mariadb["service"]

   The service name reported by default for MariaDB spans.

   This option can also be set with the ``DD_MARIADB_SERVICE`` environment
   variable.

   Default: ``"mariadb"``


Instance Configuration
~~~~~~~~~~~~~~~~~~~~~~

To configure the mariadb integration on an per-connection basis use the
``Pin`` API::

    from ddtrace import Pin
    from ddtrace import patch

    # Make sure to patch before importing mariadb
    patch(mariadb=True)

    import mariadb.connector

    # This will report a span with the default settings
    conn = mariadb.connector.connect(user="alice", password="b0b", host="localhost", port=3306, database="test")

    # Use a pin to override the service name for this connection.
    Pin.override(conn, service="mariadb-users")

    cursor = conn.cursor()
    cursor.execute("SELECT 6*7 AS the_answer;")

"""
from ...internal.utils.importlib import require_modules


required_modules = ["mariadb"]

with require_modules(required_modules) as missing_modules:
    if not missing_modules:
        from .patch import get_version
        from .patch import patch
        from .patch import unpatch

        __all__ = ["patch", "unpatch", "get_version"]
