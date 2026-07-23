"""
The LiteLLM integration instruments the LiteLLM Python SDK and proxy server.

All traces submitted from the LiteLLM integration are tagged by:

- ``service``, ``env``, ``version``: see the `Unified Service Tagging docs <https://docs.datadoghq.com/getting_started/tagging/unified_service_tagging>`_.
- ``litellm.request.model``: Model used in the request. This may be just the model name (e.g. ``gpt-3.5-turbo``) or the model name with the route defined (e.g. ``openai/gpt-3.5-turbo``).
- ``litellm.request.host``: Host where the request is sent (if specified).


Enabling
~~~~~~~~

The LiteLLM integration is enabled automatically when you use
:ref:`ddtrace-run<ddtracerun>` or :ref:`import ddtrace.auto<ddtraceauto>`.

Alternatively, use :func:`patch() <ddtrace.patch>` to manually enable the LiteLLM integration::

    from ddtrace import patch

    patch(litellm=True)


Configuration
~~~~~~~~~~~~~

.. py:data:: ddtrace.config.litellm["service"]

   The service name reported by default for LiteLLM requests.

   Alternatively, set this option with the ``DD_LITELLM_SERVICE`` environment variable.

.. py:data:: ddtrace.config.litellm["provider_metrics_enabled"]

   Whether to emit the experimental Open Trajectory LiteLLM metric suite. The
   provider profiles include operation duration, input and output token usage,
   time to first chunk, time per subsequent chunk, and authoritative
   provider-reported or LiteLLM-calculated cost. Router calls additionally emit
   logical request duration, observed provider operations with partial coverage,
   explicit retry and fallback counts, and explicit cache hits.

   Histograms map to DogStatsD distributions and cache operations map to counts.
   Metrics are emitted independently of trace export and do not include prompts,
   responses, request IDs, users, tenants, turns, or sessions. Error types use
   the bounded ``litellm.<snake_case_exception_class>`` form. Missing retry,
   fallback, or cache facts are omitted rather than inferred.

   This option is disabled by default. Enable it with the
   ``DD_LITELLM_PROVIDER_METRICS_ENABLED`` environment variable.
"""  # noqa: E501
