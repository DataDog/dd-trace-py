"""The mysqldb integration instruments the mysqlclient library to trace MySQL queries.


Enabling
~~~~~~~~

The integration is enabled automatically when using
:ref:`ddtrace-run<ddtracerun>` or :func:`patch_all()<ddtrace.patch_all>`.

Or use :func:`patch()<ddtrace.patch>` to manually enable the integration::

    from ddtrace import patch
    patch(mysqldb=True)


Global Configuration
~~~~~~~~~~~~~~~~~~~~

.. py:data:: ddtrace.config.mysqldb["service"]

   The service name reported by default for spans.

   This option can also be set with the ``DD_MYSQLDB_SERVICE`` environment
   variable.

   Default: ``"mysql"``

.. py:data:: ddtrace.config.mysqldb["trace_fetch_methods"]

   Whether or not to trace fetch methods.

   Can also configured via the ``DD_MYSQLDB_TRACE_FETCH_METHODS`` environment variable.

   Default: ``False``

.. _mysqldb_config_trace_connect:

.. py:data:: ddtrace.config.mysqldb["trace_connect"]

   Whether or not to trace connecting.

   Can also be configured via the ``DD_MYSQLDB_TRACE_CONNECT`` environment variable.

   Note that if you are overriding the service name via the Pin on an individual cursor, that will not affect
   connect traces. The service name must also be overridden on the Pin on the MySQLdb module.

   Default: ``False``


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


This package works for mysqlclient. Only the default full-Python integration works. The binary C connector provided by
_mysql is not supported.

Help on mysqlclient can be found on:
https://mysqlclient.readthedocs.io/

"""
from ...internal.utils.importlib import require_modules


required_modules = ["MySQLdb"]

with require_modules(required_modules) as missing_modules:
    if not missing_modules:
        from .patch import patch

        __all__ = ["patch"]
