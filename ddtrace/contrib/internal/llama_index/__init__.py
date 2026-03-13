"""
The LlamaIndex integration instruments the LlamaIndex Python library to trace LLM calls,
including chat completions, text completions, and streaming responses.

All traces submitted from the LlamaIndex integration are tagged by:

- ``service``, ``env``, ``version``: see the `Unified Service Tagging docs <https://docs.datadoghq.com/getting_started/tagging/unified_service_tagging>`_.
- ``llama_index.request.model``: Model used in the request.


Enabling
~~~~~~~~

The LlamaIndex integration is enabled automatically when you use
:ref:`ddtrace-run<ddtracerun>` or :ref:`import ddtrace.auto<ddtraceauto>`.
"""  # noqa: E501
