"""
The pyodbc integration instruments the pyodbc library to trace pyodbc queries.


Enabling
~~~~~~~~

The integration is enabled automatically when using
:ref:`ddtrace-run<ddtracerun>` or :ref:`import ddtrace.auto<ddtraceauto>`.

Or use :func:`patch()<ddtrace.patch>` to manually enable the integration::

    from ddtrace import patch
    patch(pyodbc=True)


Global Configuration
~~~~~~~~~~~~~~~~~~~~

.. py:data:: ddtrace.config.pyodbc["service"]

   The service name reported by default for pyodbc spans.

   This option can also be set with the ``DD_PYODBC_SERVICE`` environment
   variable.

   Default: ``"pyodbc"``

.. py:data:: ddtrace.config.pyodbc["trace_fetch_methods"]

   Whether or not to trace fetch methods.

   Can also configured via the ``DD_PYODBC_TRACE_FETCH_METHODS`` environment variable.

   Default: ``False``


Instance Configuration
~~~~~~~~~~~~~~~~~~~~~~

To configure the integration on an per-connection basis use the
``Pin`` API::

    from ddtrace.trace import Pin
    import pyodbc

    # This will report a span with the default settings
    db = pyodbc.connect("<connection string>")

    # Use a pin to override the service name for the connection.
    Pin.override(db, service='pyodbc-users')

    cursor = db.cursor()
    cursor.execute("select * from users where id = 1")
"""


# Required to allow users to import from  `ddtrace.contrib.pyodbc.patch` directly
import warnings as _w


with _w.catch_warnings():
    _w.simplefilter("ignore", DeprecationWarning)
    from . import patch as _  # noqa: F401, I001


from ddtrace.contrib.internal.pyodbc.patch import get_version  # noqa: F401
from ddtrace.contrib.internal.pyodbc.patch import patch  # noqa: F401
