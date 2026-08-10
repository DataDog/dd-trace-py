"""
The MistralAI integration instruments the MistralAI Python SDK to trace LLM requests made to
MistralAI models.

All traces submitted from the MistralAI integration are tagged by:

- ``service``, ``env``, ``version``: see the `Unified Service Tagging docs <https://docs.datadoghq.com/getting_started/tagging/unified_service_tagging>`_.
- model used in the request.
- provider used in the request.


Enabling
~~~~~~~~

The MistralAI integration is enabled automatically when you use
:ref:`ddtrace-run<ddtracerun>` or :ref:`import ddtrace.auto<ddtraceauto>`.

Alternatively, use :func:`patch() <ddtrace.patch>` to manually enable the MistralAI integration::

    from ddtrace import config, patch

    patch(mistralai=True)

Configuration
~~~~~~~~~~~~~

.. py:data:: ddtrace.config.mistralai["service"]

   The service name reported by default for MistralAI requests.
"""
