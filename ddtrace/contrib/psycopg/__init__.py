"""
The psycopg integration instruments the psycopg2 library to trace Postgres queries.


Enabling
~~~~~~~~

The psycopg integration is enabled automatically when using
:ref:`ddtrace-run<ddtracerun>` or :ref:`patch_all()<patch_all>`.

Or use :ref:`patch()<patch>` to manually enable the integration::

    from ddtrace import patch
    patch(psycopg=True)


Global Configuration
~~~~~~~~~~~~~~~~~~~~

.. py:data:: ddtrace.config.psycopg["service"]

   The service name reported by default for psycopg spans.

   This option can also be set with the ``DD_PSYCOPG_SERVICE`` environment
   variable.

   Default: ``"postgres"``

.. py:data:: ddtrace.config.psycopg["trace_fetch_methods"]

   Whether or not to trace fetch methods.

   Can also configured via the ``DD_PSYCOPG_TRACE_FETCH_METHODS`` environment variable.

   Default: ``False``


Instance Configuration
~~~~~~~~~~~~~~~~~~~~~~

To configure the psycopg integration on an per-connection basis use the
``Pin`` API::

    from ddtrace import Pin
    import psycopg2

    db = psycopg2.connect(connection_factory=factory)
    # Use a pin to override the service name.
    Pin.override(db, service="postgres-users")

    cursor = db.cursor()
    cursor.execute("select * from users where id = 1")
"""
from ...internal.utils.importlib import require_modules


required_modules = ["psycopg2"]

with require_modules(required_modules) as missing_modules:
    if not missing_modules:
        from .connection import connection_factory
        from .patch import patch
        from .patch import patch_conn

        __all__ = ["connection_factory", "patch", "patch_conn"]
