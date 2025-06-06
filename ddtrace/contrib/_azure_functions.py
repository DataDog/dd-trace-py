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

   The service name reported by default for azure_functions instances.

   This option can also be set with the ``DD_SERVICE`` environment
   variable.

   Default: ``"azure_functions"``

   # TODO: distributed tracing config
"""
