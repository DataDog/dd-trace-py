"""
The pymysql integration instruments the pymysql library to trace MySQL queries.


Enabling
~~~~~~~~

The integration is enabled automatically when using
:ref:`ddtrace-run<ddtracerun>` or :ref:`import ddtrace.auto<ddtraceauto>`.

Or use :func:`patch()<ddtrace.patch>` to manually enable the integration::

    from ddtrace import patch
    patch(pymysql=True)


Configuration
~~~~~~~~~~~~~

.. py:data:: ddtrace.config.pymysql["service"]

   The service name reported by default for pymysql spans.

   This option can also be set with the ``DD_PYMYSQL_SERVICE`` environment
   variable.

   Default: ``"mysql"``

.. py:data:: ddtrace.config.pymysql["trace_fetch_methods"]

   Whether or not to trace fetch methods.

   Can also configured via the ``DD_PYMYSQL_TRACE_FETCH_METHODS`` environment variable.

   Default: ``False``
"""
