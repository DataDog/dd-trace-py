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

   The service name reported by default for azure_servicebus clients.

   This option can also be set with the ``DD_SERVICE`` environment
   variable.

   Default: ``"azure_servicebus"``

"""
