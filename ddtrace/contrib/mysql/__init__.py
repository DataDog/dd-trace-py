"""
The mysql integration instruments the mysql library to trace MySQL queries.


Enabling
~~~~~~~~

The mysql integration is enabled automatically when using
:ref:`ddtrace-run<ddtracerun>` or :ref:`patch_all()<patch_all>`.

Or use :ref:`patch()<patch>` to manually enable the integration::

    from ddtrace import patch
    patch(mysql=True)


Global Configuration
~~~~~~~~~~~~~~~~~~~~

.. py:data:: ddtrace.config.mysql["service"]

   The service name reported by default for mysql spans.

   This option can also be set with the ``DD_MYSQL_SERVICE`` environment
   variable.

   Default: ``"mysql"``


Instance Configuration
~~~~~~~~~~~~~~~~~~~~~~

To configure the mysql integration on an per-connection basis use the
``Pin`` API::

    from ddtrace import Pin
    # Make sure to import mysql.connector and not the 'connect' function,
    # otherwise you won't have access to the patched version
    import mysql.connector

    # This will report a span with the default settings
    conn = mysql.connector.connect(user="alice", password="b0b", host="localhost", port=3306, database="test")

    # Use a pin to override the service name for this connection.
    Pin.override(conn, service='mysql-users')

    cursor = conn.cursor()
    cursor.execute("SELECT 6*7 AS the_answer;")


Only the default full-Python integration works. The binary C connector,
provided by _mysql_connector, is not supported.

Help on mysql.connector can be found on:
https://dev.mysql.com/doc/connector-python/en/
"""
from ...utils.importlib import require_modules


# check `mysql-connector` availability
required_modules = ["mysql.connector"]

with require_modules(required_modules) as missing_modules:
    if not missing_modules:
        from .patch import patch
        from .tracers import get_traced_mysql_connection

        __all__ = ["get_traced_mysql_connection", "patch"]
