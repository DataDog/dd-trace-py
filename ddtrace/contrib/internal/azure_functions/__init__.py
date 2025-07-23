"""
The azure_functions integration traces all http requests to your Azure Function app.

Enabling
~~~~~~~~

Use :func:`patch()<ddtrace.patch>` to manually enable the integration::

    from ddtrace import patch
    patch(azure_functions=True)


Global Configuration
~~~~~~~~~~~~~~~~~~~~

.. py:data:: ddtrace.config.azure_functions["service"]

   The service name reported by default for azure function apps.

   This option can also be set with the ``DD_SERVICE`` environment
   variable.

   Default: ``"azure_functions"``

.. py:data:: ddtrace.config.azure_functions['distributed_tracing']

   Whether to parse distributed tracing headers from requests or messages received by your azure function apps.

   This option can also be set with the ``DD_AZURE_FUNCTIONS_DISTRIBUTED_TRACING``
   environment variable.

   Default: ``True``
"""
