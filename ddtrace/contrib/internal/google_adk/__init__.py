"""
The Google ADK integration instruments the Google ADK Python SDK to create spans for Agent requests.

All traces submitted from the Google ADK integration are tagged by:

- ``service``, ``env``, ``version``: see the `Unified Service Tagging docs <https://docs.datadoghq.com/getting_started/tagging/unified_service_tagging>`_.
- model used in the request.
- provider used in the request.


Enabling
~~~~~~~~

The Google ADK integration is enabled automatically when you use
:ref:`ddtrace-run<ddtracerun>` or :ref:`import ddtrace.auto<ddtraceauto>`.

Global Configuration
~~~~~~~~~~~~~~~~~~~~

.. py:data:: ddtrace.config.google_adk["service"]

   The service name reported by default for Google ADK requests.

   Alternatively, you can set this option with the ``DD_SERVICE`` or ``DD_GOOGLE_ADK_SERVICE`` environment
   variables.

   Default: ``DD_SERVICE``

"""
