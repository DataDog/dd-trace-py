"""
The valkey integration traces valkey requests.


Enabling
~~~~~~~~

The valkey integration is enabled automatically when using
:ref:`ddtrace-run<ddtracerun>` or :ref:`import ddtrace.auto<ddtraceauto>`.

Or use :func:`patch()<ddtrace.patch>` to manually enable the integration::

    from ddtrace import patch
    patch(valkey=True)


Global Configuration
~~~~~~~~~~~~~~~~~~~~

.. py:data:: ddtrace.config.valkey["service"]

   The service name reported by default for valkey traces.

   This option can also be set with the ``DD_VALKEY_SERVICE`` environment
   variable.

   Default: ``"valkey"``


.. py:data:: ddtrace.config.valkey["cmd_max_length"]

   Max allowable size for the valkey command span tag.
   Anything beyond the max length will be replaced with ``"..."``.

   This option can also be set with the ``DD_VALKEY_CMD_MAX_LENGTH`` environment
   variable.

   Default: ``1000``


.. py:data:: ddtrace.config.valkey["resource_only_command"]

   The span resource will only include the command executed. To include all
   arguments in the span resource, set this value to ``False``.

   This option can also be set with the ``DD_VALKEY_RESOURCE_ONLY_COMMAND`` environment
   variable.

   Default: ``True``


Instance Configuration
~~~~~~~~~~~~~~~~~~~~~~

To configure particular valkey instances use the :class:`Pin <ddtrace.Pin>` API::

    import valkey
    from ddtrace.trace import Pin

    client = valkey.StrictValkey(host="localhost", port=6379)

    # Override service name for this instance
    Pin.override(client, service="my-custom-queue")

    # Traces reported for this client will now have "my-custom-queue"
    # as the service name.
    client.get("my-key")
"""
