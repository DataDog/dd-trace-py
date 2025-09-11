"""
The Google ADK integration instruments the Google ADK Python SDK to trace Agent requests.

All traces submitted from the Google ADK integration are tagged by:

- ``service``, ``env``, ``version``: see the `Unified Service Tagging docs <https://docs.datadoghq.com/getting_started/tagging/unified_service_tagging>`_.
- model used in the request.
- provider used in the request.


Enabling
~~~~~~~~

The Google ADK integration is enabled automatically when you use
:ref:`ddtrace-run<ddtracerun>` or :ref:`import ddtrace.auto<ddtraceauto>`.

Alternatively, use :func:`patch() <ddtrace.patch>` to manually enable the Google ADK integration::

    from ddtrace import config, patch

    patch(google_adk=True)

Global Configuration
~~~~~~~~~~~~~~~~~~~~

.. py:data:: ddtrace.config.google_adk["service"]

   The service name reported by default for Google ADK requests.

   Alternatively, you can set this option with the ``DD_SERVICE`` or ``DD_GOOGLE_ADK_SERVICE`` environment
   variables.

   Default: ``DD_SERVICE``


Instance Configuration
~~~~~~~~~~~~~~~~~~~~~~

To configure the Google ADK integration on a per-instance basis use the
``Pin`` API::

    from google import adk
    from ddtrace.trace import Pin

    Pin.override(adk, service="my-google-adk-service")
"""
