"""
The Google GenAI integration instruments the Google GenAI Python SDK to trace LLM requests made to
Gemini and VertexAI models.

All traces submitted from the Google GenAI integration are tagged by:

- ``service``, ``env``, ``version``: see the `Unified Service Tagging docs <https://docs.datadoghq.com/getting_started/tagging/unified_service_tagging>`_.
- model used in the request.
- provider used in the request.


Enabling
~~~~~~~~

The Google GenAI integration is enabled automatically when you use
:ref:`ddtrace-run<ddtracerun>` or :ref:`import ddtrace.auto<ddtraceauto>`.

Alternatively, use :func:`patch() <ddtrace.patch>` to manually enable the Google GenAI integration::

    from ddtrace import config, patch

    patch(google_genai=True)

Global Configuration
~~~~~~~~~~~~~~~~~~~~

.. py:data:: ddtrace.config.google_genai["service"]

   The service name reported by default for Google GenAI requests.

   Alternatively, you can set this option with the ``DD_SERVICE`` or ``DD_GOOGLE_GENAI_SERVICE`` environment
   variables.

   Default: ``DD_SERVICE``


Instance Configuration
~~~~~~~~~~~~~~~~~~~~~~

To configure the Google GenAI integration on a per-instance basis use the
``Pin`` API::

    from google import genai
    from ddtrace.trace import Pin

    Pin.override(genai, service="my-google-genai-service")
"""
