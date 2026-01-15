"""
The aiomysql integration instruments the aiomysql library to trace MySQL queries.

Enabling
~~~~~~~~

The integration is enabled automatically when using
:ref:`ddtrace-run<ddtracerun>` or :ref:`import ddtrace.auto<ddtraceauto>`.

Or use :func:`patch()<ddtrace.patch>` to manually enable the integration::

    from ddtrace import patch
    patch(aiomysql=True)


Configuration
~~~~~~~~~~~~~

.. py:data:: ddtrace.config.aiomysql["service"]

   The service name reported by default for aiomysql spans.

   This option can also be set with the ``DD_AIOMYSQL_SERVICE`` environment
   variable.

   Default: ``"mysql"``
"""
