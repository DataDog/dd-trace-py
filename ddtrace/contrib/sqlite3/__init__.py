"""
The sqlite integration instruments the built-in sqlite module to trace SQLite queries.


Enabling
~~~~~~~~

The integration is enabled automatically when using
:ref:`ddtrace-run<ddtracerun>` or :ref:`patch_all()<patch_all>`.

Or use :ref:`patch()<patch>` to manually enable the integration::

    from ddtrace import patch
    patch(sqlite=True)


Global Configuration
~~~~~~~~~~~~~~~~~~~~

.. py:data:: ddtrace.config.sqlite["service"]

   The service name reported by default for sqlite spans.

   This option can also be set with the ``DD_SQLITE_SERVICE`` environment
   variable.

   Default: ``"sqlite"``


Instance Configuration
~~~~~~~~~~~~~~~~~~~~~~

To configure the integration on an per-connection basis use the
``Pin`` API::

    from ddtrace import Pin
    import sqlite3

    # This will report a span with the default settings
    db = sqlite3.connect(":memory:")

    # Use a pin to override the service name for the connection.
    Pin.override(db, service='sqlite-users')

    cursor = db.cursor()
    cursor.execute("select * from users where id = 1")
"""
from .connection import connection_factory
from .patch import patch


__all__ = ["connection_factory", "patch"]
