"""
The sqlite integration instruments the built-in sqlite module to trace SQLite queries.

The ``execute()``, ``executemany()``, and ``executescript()`` methods are instrumented
on both connections and cursors. Connection shortcuts preserve custom connection
methods and return instrumented cursor proxies, matching ``Connection.execute()``.
Each script is traced as a single query operation. Proxies support the cursor
interface but cannot be passed to APIs that require an exact native cursor type.
The ``cursor.connection`` attribute retains the native connection; calls through
that connection are not instrumented.


Enabling
~~~~~~~~

The integration is enabled automatically when using
:ref:`ddtrace-run<ddtracerun>` or :ref:`import ddtrace.auto<ddtraceauto>`.

Or use :func:`patch()<ddtrace.patch>` to manually enable the integration::

    from ddtrace import patch
    patch(sqlite=True)


Configuration
~~~~~~~~~~~~~

.. py:data:: ddtrace.config.sqlite["service"]

   The service name reported by default for sqlite spans.

   This option can also be set with the ``DD_SQLITE_SERVICE`` environment
   variable.

   Default: ``"sqlite"``

.. py:data:: ddtrace.config.sqlite["trace_fetch_methods"]

   Whether or not to trace fetch methods.

   Can also configured via the ``DD_SQLITE_TRACE_FETCH_METHODS`` environment variable.

   Default: ``False``
"""
