"""
The azure_durable_functions integration traces durable activity and entity functions.

Enabling
~~~~~~~~

Use :func:`patch()<ddtrace.patch>` to manually enable the integration::

    from ddtrace import patch
    patch(azure_durable_functions=True)


Global Configuration
~~~~~~~~~~~~~~~~~~~~

This integration shares configuration with the ``azure_functions`` integration.

.. py:data:: ddtrace.config.azure_functions["service"]

   The service name reported by default for Azure function apps.

   This option can also be set with the ``DD_SERVICE`` environment
   variable.

   Default: ``"azure_functions"``
"""
