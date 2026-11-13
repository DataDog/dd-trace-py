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


Usage and cost metrics
~~~~~~~~~~~~~~~~~~~~~~

For calls made through a LiteLLM proxy, the integration can emit token usage and cost metrics for each provider
attempt and each client request. They contain no prompts, responses or keys. See
`LiteLLM proxy usage and cost metrics <https://docs.datadoghq.com/cloud_cost_management/ai_costs/litellm_proxy_metrics/>`_
for the metrics, their tags, and how to join them to a provider's bill.

.. py:data:: ddtrace.config.litellm["usage_metrics_enabled"]

   Emit usage and cost metrics for LiteLLM proxy calls.

   Alternatively, set this option with the ``DD_LITELLM_USAGE_METRICS_ENABLED`` environment variable.

   Default: ``False``

.. py:data:: ddtrace.config.litellm["usage_metrics_exporter"]

   ``dogstatsd`` sends the metrics to the Datadog Agent's DogStatsD endpoint (``DD_DOGSTATSD_URL``), where the
   counters are stored as rate metrics and the durations as distributions, as every other source of these metrics
   sends them.

   ``otlp`` sends OTLP/HTTP metrics to ``OTEL_EXPORTER_OTLP_METRICS_ENDPOINT`` (or ``OTEL_EXPORTER_OTLP_ENDPOINT``),
   or without one to the Datadog Agent's OTLP receiver, or to Datadog's OTLP intake when ``DD_AGENTLESS_ENABLED`` is
   true. It is meant for backends other than Datadog: Datadog stores these OTLP counters as count metrics, which
   conflict with the rate metrics other sources, such as AI gateways, send under the same names, and Datadog keeps
   one type per metric.

   Alternatively, set this option with the ``DD_LITELLM_USAGE_METRICS_EXPORTER`` environment variable.

   Default: ``dogstatsd``

.. py:data:: ddtrace.config.litellm["usage_metrics_tags"]

   A comma-separated list of optional tags to add: ``user``, ``team``, ``key_alias``, ``route``, ``destination``,
   ``provider_account``, ``project``, ``service`` and ``host``. Each can raise the number of series.

   Alternatively, set this option with the ``DD_LITELLM_USAGE_METRICS_TAGS`` environment variable.

   Default: ``None``

.. py:data:: ddtrace.config.litellm["usage_metrics_client_source"]

   The name of the client application using this proxy, sent as ``trajectory.client_source`` when set.

   Alternatively, set this option with the ``DD_LITELLM_USAGE_METRICS_CLIENT_SOURCE`` environment variable.

   Default: ``None``
"""  # noqa: E501
