"""
The DuckDB integration instruments the DuckDB library.


Enabling
~~~~~~~~

The DuckDB integration is enabled automatically when using
:ref:`ddtrace-run <ddtracerun>` or :ref:`import ddtrace.auto <ddtraceauto>`.

Or use :func:`patch() <ddtrace.patch>` to manually enable the integration::

    from ddtrace import patch
    patch(duckdb=True)


Global Configuration
~~~~~~~~~~~~~~~~~~~~

.. py:data:: ddtrace.config.duckdb["service"]

   The service name reported by default for DuckDB instances.

   This option can also be set with the ``DD_DUCKDB_SERVICE`` environment
   variable.

   Default: ``"duckdb"``


Instance Configuration
~~~~~~~~~~~~~~~~~~~~~~

To configure the DuckDB integration on an per-instance basis use the
``Pin`` API::

    from ddtrace.trace import Pin
    from ddtrace import patch

    # Make sure to patch before importing duckdb
    patch(duckdb=True)

    import duckdb

    # This will report a span with the default settings
    conn = duckdb.connect(database=":memory:")

    # Use a pin to override the service name for this connection.
    Pin.override(conn, service="duckdb")

    # Insert data into the database
    conn.execute("CREATE TABLE test (id INTEGER, name VARCHAR)")
    conn.execute("INSERT INTO test VALUES (1, 'Alice')")
    conn.execute("INSERT INTO test VALUES (2, 'Bob')")

    # Query the data
    cursor = conn.cursor()
    cursor.execute("SELECT *")
    print(cursor.fetchall())
"""

from .patch import get_version
from .patch import patch
from .patch import unpatch


__all__ = ["patch", "unpatch", "get_version"]
