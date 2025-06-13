"""
The azure_servicebus integration traces all messages sent by the service bus client.

Enabling
~~~~~~~~

Use :func:`patch()<ddtrace.patch>` to manually enable the integration::

    from ddtrace import patch
    patch(azure_servicebus=True)


Global Configuration
~~~~~~~~~~~~~~~~~~~~

.. py:data:: ddtrace.config.azure_servicebus["service"]

   The service name reported by default for azure service bus clients.

   This option can also be set with the ``DD_AZURE_SERVICEBUS_SERVICE`` environment
   variable.

   Default: ``"azure_servicebus"``

.. py:data:: ddtrace.config.azure_servicebus['distributed_tracing']

   Include distributed tracing headers in service bus messages sent from the azure service bus client.

   This option can also be set with the ``DD_AZURE_SERVICEBUS_DISTRIBUTED_TRACING``
   environment variable.

   Default: ``True``
"""
