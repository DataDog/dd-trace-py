"""
The azure_eventhubs integration traces all events sent by the Azure Event Hubs client.

Enabling
~~~~~~~~

The azure_eventhubs integration is enabled by default when using :ref:`import ddtrace.auto<ddtraceauto>`.


Global Configuration
~~~~~~~~~~~~~~~~~~~~

.. py:data:: ddtrace.config.azure_eventhubs["service"]

   The service name reported by default for azure event hubs clients.

   This option can also be set with the ``DD_AZURE_EVENTHUBS_SERVICE`` environment
   variable.

   Default: ``"azure_eventhubs"``

.. py:data:: ddtrace.config.azure_eventhubs['distributed_tracing']

 Include distributed tracing headers in event hubs events sent from the Azure Event Hubs client.

   This option can also be set with the ``DD_AZURE_EVENTHUBS_DISTRIBUTED_TRACING``
   environment variable.

   Default: ``True``

.. py:data:: ddtrace.config.azure_eventhubs['batch_links']

   Add create spans and span links for event hubs events added to and sent in a batch.

   This option can also be set with the ``DD_TRACE_AZURE_EVENTHUBS_BATCH_LINKS_ENABLED``
   environment variable.

   Distributed tracing must also be enabled for span links to be added.

   Default: ``True``
"""
