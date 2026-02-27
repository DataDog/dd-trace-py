"""
The MLFlow integration provides authentication for MLFlow requests using Datadog API keys.

This integration automatically injects Datadog API and application keys into MLFlow HTTP
requests when the ``DD_API_KEY`` and ``DD_APP_KEY`` environment variables are set.


Enabling
~~~~~~~~

The MLFlow authentication plugin is registered via the MLFlow plugin system and will be
automatically available when this package is installed. To activate it, MLFlow must be
configured to use the Datadog header provider. This happens automatically when the
plugin is installed.


Configuration
~~~~~~~~~~~~~

.. py:data:: DD_API_KEY and DD_APP_KEY

   Environment variables containing the Datadog API and app keys to inject into MLFlow requests.

   If these environment variables are set, all MLFlow HTTP requests will include
   ``DD-API-KEY`` and ``DD-APPLICATION-KEY`` headers with the values from these
   environment variables.

   Default: Not set (authentication plugin is inactive)
"""
