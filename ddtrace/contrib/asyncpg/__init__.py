"""
The ``asyncpg`` integration traces database requests made using connection
and cursor objects.


Enabling
~~~~~~~~

The integration is enabled automatically when using
:ref:`ddtrace-run<ddtracerun>` or :ref:`import ddtrace.auto<ddtraceauto>`.

Or use :func:`patch()<ddtrace.patch>` to manually enable the integration::

    from ddtrace import patch
    patch(asyncpg=True)


Global Configuration
~~~~~~~~~~~~~~~~~~~~

.. py:data:: ddtrace.config.asyncpg['service']

   The service name reported by default for asyncpg connections.

   This option can also be set with the ``DD_ASYNCPG_SERVICE``
   environment variable.

   Default: ``postgres``


Instance Configuration
~~~~~~~~~~~~~~~~~~~~~~

Service
^^^^^^^

To configure the service name used by the asyncpg integration on a per-instance
basis use the ``Pin`` API::

    import asyncpg
    from ddtrace.trace import Pin

    conn = asyncpg.connect("postgres://localhost:5432")
    Pin.override(conn, service="custom-service")
"""


# Required to allow users to import from  `ddtrace.contrib.asyncpg.patch` directly
import warnings as _w


with _w.catch_warnings():
    _w.simplefilter("ignore", DeprecationWarning)
    from . import patch as _  # noqa: F401, I001

from ddtrace.contrib.internal.asyncpg.patch import get_version  # noqa: F401
from ddtrace.contrib.internal.asyncpg.patch import patch  # noqa: F401
from ddtrace.contrib.internal.asyncpg.patch import unpatch  # noqa: F401
