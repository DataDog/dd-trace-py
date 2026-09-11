"""
The azure_durable_functions integration traces durable orchestration, activity, and entity functions.

Enabling
~~~~~~~~

Use :func:`patch()<ddtrace.patch>` to manually enable the integration::

    from ddtrace import patch
    patch(azure_durable_functions=True)


Global Configuration
~~~~~~~~~~~~~~~~~~~~

This integration shares configuration with the ``azure_functions`` integration.

To connect orchestration and activity spans across Durable invocations, enable
Durable Functions distributed tracing V2 in ``host.json``::

    {
      "extensions": {
        "durableTask": {
          "tracing": {
            "distributedTracingEnabled": true,
            "version": "V2"
          }
        }
      }
    }

This does not require setting ``telemetryMode`` to ``OpenTelemetry``. Azure does
not currently propagate parent trace context to Python entity invocations, so
entity spans are traced but may appear in a separate trace.

.. py:data:: ddtrace.config.azure_functions["service"]

   The service name reported by default for Azure function apps.

   This option can also be set with the ``DD_SERVICE`` environment
   variable.

   Default: ``"azure_functions"``
"""
