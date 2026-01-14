"""
The aredis integration traces aredis requests.


Enabling
~~~~~~~~

The aredis integration is enabled automatically when using
:ref:`ddtrace-run<ddtracerun>` or :ref:`import ddtrace.auto<ddtraceauto>`.

Or use :func:`patch()<ddtrace.patch>` to manually enable the integration::

    from ddtrace import patch
    patch(aredis=True)


Configuration
~~~~~~~~~~~~~

.. py:data:: ddtrace.config.aredis["service"]

   The service name reported by default for aredis traces.

   This option can also be set with the ``DD_AREDIS_SERVICE`` environment
   variable.

   Default: ``"redis"``

.. py:data:: ddtrace.config.aredis["cmd_max_length"]

   Max allowable size for the aredis command span tag.
   Anything beyond the max length will be replaced with ``"..."``.

   This option can also be set with the ``DD_AREDIS_CMD_MAX_LENGTH`` environment
   variable.

   Default: ``1000``

.. py:data:: ddtrace.config.aredis["resource_only_command"]

   The span resource will only include the command executed. To include all
   arguments in the span resource, set this value to ``False``.

   This option can also be set with the ``DD_REDIS_RESOURCE_ONLY_COMMAND`` environment
   variable.

   Default: ``True``
"""
