"""
The mysqldb integration instruments the mysqlclient and MySQL-python  libraries to trace MySQL queries.


Enabling
~~~~~~~~

The integration is enabled automatically when using
:ref:`ddtrace-run<ddtracerun>` or :ref:`patch_all()<patch_all>`.

Or use :ref:`patch()<patch>` to manually enable the integration::

    from ddtrace import patch
    patch(mysqldb=True)


Global Configuration
~~~~~~~~~~~~~~~~~~~~

.. py:data:: ddtrace.config.mysqldb["service"]

   The service name reported by default for spans.

   This option can also be set with the ``DD_MYSQLDB_SERVICE`` environment
   variable.

   Default: ``"mysql"``


Instance Configuration
~~~~~~~~~~~~~~~~~~~~~~

To configure the integration on an per-connection basis use the
``Pin`` API::

    # Make sure to import MySQLdb and not the 'connect' function,
    # otherwise you won't have access to the patched version
    from ddtrace import Pin
    import MySQLdb

    # This will report a span with the default settings
    conn = MySQLdb.connect(user="alice", passwd="b0b", host="localhost", port=3306, db="test")

    # Use a pin to override the service.
    Pin.override(conn, service='mysql-users')

    cursor = conn.cursor()
    cursor.execute("SELECT 6*7 AS the_answer;")


This package works for mysqlclient or MySQL-python. Only the default
full-Python integration works. The binary C connector provided by
_mysql is not supported.

Help on mysqlclient can be found on:
https://mysqlclient.readthedocs.io/
"""
from ...utils.importlib import require_modules


required_modules = ["MySQLdb"]

with require_modules(required_modules) as missing_modules:
    if not missing_modules:
        from .patch import patch

        __all__ = ["patch"]
