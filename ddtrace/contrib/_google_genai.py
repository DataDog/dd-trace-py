"""
The Google GenAI integration instruments the Google GenAI Python SDK to trace for requests made to Google models.

All traces submitted from the Google GenAI integration are tagged by:

- ``service``, ``env``, ``version``: see the `Unified Service Tagging docs <https://docs.datadoghq.com/getting_started/tagging/unified_service_tagging>`_.
- ``google_generativeai.request.model``: Google model used in the request.
- ``google_generativeai.request.provider``: Google provider used in the request.

This instrumentation is still in development, and is not yet submitted to the LLMobs integration.
"""
