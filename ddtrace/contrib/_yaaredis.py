"""
The yaaredis integration traces yaaredis requests.


Enabling
~~~~~~~~

The yaaredis integration is enabled automatically when using
:ref:`ddtrace-run<ddtracerun>` or :ref:`import ddtrace.auto<ddtraceauto>`.

Or use :func:`patch()<ddtrace.patch>` to manually enable the integration::

    from ddtrace import patch
    patch(yaaredis=True)


Global Configuration
~~~~~~~~~~~~~~~~~~~~

.. py:data:: ddtrace.config.yaaredis["service"]

   The service name reported by default for yaaredis traces.

   This option can also be set with the ``DD_YAAREDIS_SERVICE`` environment
   variable.

   Default: ``"redis"``

.. py:data:: ddtrace.config.yaaredis["cmd_max_length"]

   Max allowable size for the yaaredis command span tag.
   Anything beyond the max length will be replaced with ``"..."``.

   This option can also be set with the ``DD_YAAREDIS_CMD_MAX_LENGTH`` environment
   variable.

   Default: ``1000``

.. py:data:: ddtrace.config.aredis["resource_only_command"]

   The span resource will only include the command executed. To include all
   arguments in the span resource, set this value to ``False``.

   This option can also be set with the ``DD_REDIS_RESOURCE_ONLY_COMMAND`` environment
   variable.

   Default: ``True``


Instance Configuration
~~~~~~~~~~~~~~~~~~~~~~

To configure particular yaaredis instances use the :class:`Pin <ddtrace.trace.Pin>` API::

    import yaaredis
    from ddtrace.trace import Pin

    client = yaaredis.StrictRedis(host="localhost", port=6379)

    # Override service name for this instance
    Pin.override(client, service="my-custom-queue")

    # Traces reported for this client will now have "my-custom-queue"
    # as the service name.
    async def example():
        await client.get("my-key")
"""
