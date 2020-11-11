"""
The pyodbc integration instruments the pyodbc library to trace pyodbc queries.


Enabling
~~~~~~~~

The integration is enabled automatically when using
:ref:`ddtrace-run<ddtracerun>` or :ref:`patch_all()<patch_all>`.

Or use :ref:`patch()<patch>` to manually enable the integration::

    from ddtrace import patch
    patch(pyodbc=True)


Global Configuration
~~~~~~~~~~~~~~~~~~~~

.. py:data:: ddtrace.config.pyodbc["service"]

   The service name reported by default for pyodbc spans.

   This option can also be set with the ``DD_PYODBC_SERVICE`` environment
   variable.

   Default: ``"pyodbc"``


Instance Configuration
~~~~~~~~~~~~~~~~~~~~~~

To configure the integration on an per-connection basis use the
``Pin`` API::

    from ddtrace import Pin
    import pyodbc

    # This will report a span with the default settings
    db = pyodbc.connect("<connection string>")

    # Use a pin to override the service name for the connection.
    Pin.override(db, service='pyodbc-users')

    cursor = db.cursor()
    cursor.execute("select * from users where id = 1")
"""
from ...utils.importlib import require_modules


required_modules = ["pyodbc"]

with require_modules(required_modules) as missing_modules:
    if not missing_modules:
        from .patch import patch

        __all__ = ["patch"]
