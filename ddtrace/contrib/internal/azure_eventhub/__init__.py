"""
The azure_eventhub integration traces all messages sent by the event hub client.

Enabling
~~~~~~~~

Use :func:`patch()<ddtrace.patch>` to manually enable the integration::

    from ddtrace import patch
    patch(azure_eventhub=True)


Global Configuration
~~~~~~~~~~~~~~~~~~~~

.. py:data:: ddtrace.config.azure_eventhub["service"]

   The service name reported by default for azure event hub clients.

   This option can also be set with the ``DD_AZURE_EVENTHUB_SERVICE`` environment
   variable.

   Default: ``"azure_eventhub"``

.. py:data:: ddtrace.config.azure_eventhub['distributed_tracing']

   Include distributed tracing headers in event hub messages sent from the azure event hub client.

   This option can also be set with the ``DD_AZURE_EVENTHUB_DISTRIBUTED_TRACING``
   environment variable.

   Default: ``True``
"""
