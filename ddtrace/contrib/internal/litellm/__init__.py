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

For calls made through a LiteLLM proxy, the integration can emit usage and cost metrics that follow the
`OpenTelemetry GenAI semantic conventions <https://opentelemetry.io/docs/specs/semconv/gen-ai/gen-ai-metrics/>`_.
They are recorded for chat completions, text completions, Responses API calls, Anthropic Messages calls and embeddings,
whether or not a trace is sent, and contain no prompts or responses. Other calls, such as image generation, audio,
rerank and batch requests, record nothing.

For each request from the proxy to a model provider, including each retry and fallback:

- ``gen_ai.client.inference.duration`` (``gen_ai.client.operation.duration`` for embeddings), with ``error.type`` on failures.
- ``gen_ai.client.inference.usage.input_tokens`` and ``gen_ai.client.inference.usage.output_tokens``. Input tokens include
  cached input. A count the provider did not report is tagged ``trajectory.token.source:estimated``.
- ``gen_ai.client.inference.usage.cache_read.input_tokens`` and ``gen_ai.client.inference.usage.cache_write.input_tokens``.
- ``trajectory.gen_ai.client.inference.usage.cost``: the proxy's own price for the call, tagged
  ``trajectory.cost.source:estimated``.

The token and cost metrics of a call carry ``trajectory.request.service_tier`` when the provider's response names the
tier it served the call at, in the provider's own words (for example ``default`` or ``flex`` for OpenAI, ``standard``
for Anthropic). The tier a request asked for is never used, and a streamed chat completion has none.

For each client request to the proxy, ``trajectory.gen_ai.gateway.request.estimated_cost`` is the amount the proxy charged
against its budgets. The two cost metrics describe the same spend and are never added together. Every point is tagged
``gen_ai.operation.name``, ``gen_ai.request.model`` and ``trajectory.observation.point:gateway``; provider metrics also carry
``gen_ai.provider.name`` and ``gen_ai.response.model``. Values that are not observed are omitted, never reported as zero.

.. py:data:: ddtrace.config.litellm["usage_metrics_enabled"]

   Emit usage and cost metrics for LiteLLM proxy calls.

   Alternatively, set this option with the ``DD_LITELLM_USAGE_METRICS_ENABLED`` environment variable.

   Default: ``False``

.. py:data:: ddtrace.config.litellm["usage_metrics_exporter"]

   ``otlp`` sends OTLP/HTTP protobuf metrics with delta temporality to the endpoint given by
   ``OTEL_EXPORTER_OTLP_METRICS_ENDPOINT`` (or ``OTEL_EXPORTER_OTLP_ENDPOINT`` followed by ``/v1/metrics``), with the
   headers in ``OTEL_EXPORTER_OTLP_METRICS_HEADERS``. Without an endpoint, metrics go to Datadog's OTLP intake
   (``https://otlp.<DD_SITE>/v1/metrics``, with ``DD_API_KEY`` as the ``dd-api-key`` header) when agentless mode is on
   (``DD_AGENTLESS_ENABLED=true``), and otherwise to the Datadog Agent's OTLP receiver on port 4318, which the Agent
   enables only when configured (for example with ``DD_OTLP_CONFIG_RECEIVER_PROTOCOLS_HTTP_ENDPOINT=0.0.0.0:4318``).
   The API key is sent only to the intake. A failing export logs a warning. An endpoint configured for gRPC (``OTEL_EXPORTER_OTLP_METRICS_PROTOCOL``, or else
   ``OTEL_EXPORTER_OTLP_PROTOCOL``, set to ``grpc``) is not supported: set ``OTEL_EXPORTER_OTLP_METRICS_PROTOCOL`` to
   ``http/protobuf`` with an OTLP/HTTP ``OTEL_EXPORTER_OTLP_METRICS_ENDPOINT``, or the metrics are not enabled. An export
   that fails with status 429, 502, 503 or 504, a refused connection or a timeout is retried, and kept for later exports
   while the endpoint stays unavailable. ``dogstatsd`` sends the same series to the Datadog Agent's DogStatsD endpoint.

   Without a configured endpoint, or with Datadog's OTLP intake configured, Datadog stores the export as it arrives,
   so cost is sent in USD, the same values ``dogstatsd`` sends. An export to another configured endpoint, such as an
   OpenTelemetry Collector, follows the semantic conventions and sends cost as an integer number of billionths of a
   dollar (unit ``{nanoUSD}``).

   Alternatively, set this option with the ``DD_LITELLM_USAGE_METRICS_EXPORTER`` environment variable.

   Default: ``otlp``

.. py:data:: ddtrace.config.litellm["usage_metrics_tags"]

   A comma-separated list of the optional tags to add. Each can raise the number of series.

   - ``user``: ``user.id``, the user who owns the proxy key.
   - ``team``: ``trajectory.team.id``, the team of the proxy key.
   - ``key_alias``: ``trajectory.gateway.key.alias``, the alias of the proxy key. Keys and key hashes are never sent.
   - ``route``: ``trajectory.gateway.route``, the model group the client asked for.
   - ``destination``: ``trajectory.gateway.destination.id``, the deployment the proxy chose.
   - ``provider_account``: ``trajectory.request.provider_account_id``, the account at the provider that served the
     call: the ``anthropic-organization-id`` response header for Anthropic, the ``openai-organization`` response header
     for OpenAI, which is the organization's name rather than its ``org-`` identifier, and the AWS account of a
     Bedrock model given as an ARN, such as an inference profile's.
   - ``project``: ``trajectory.request.project_id``, the provider's project: the ``openai-project`` response header
     for OpenAI.
   - ``service``, ``host``: ``service.name`` and ``host.name`` of this process.

   No other response header is read. Ids that a deployment declares in its ``model_info`` are always sent, and take
   the place of the ones found here.

   Alternatively, set this option with the ``DD_LITELLM_USAGE_METRICS_TAGS`` environment variable.

   Default: ``None``

To join these metrics to a provider's bill, a deployment can declare the ids that the proxy cannot discover in its
``model_info``, as the provider's console shows them. Each is sent, exactly as written, on the metrics of every call to
that deployment:

- ``datadog_api_key_id``: ``trajectory.request.api_key_id``, the provider's identifier of the API key the deployment
  uses. Write the identifier the provider's console lists for the key, never the key itself: the integration does not
  derive it from the key.
- ``datadog_provider_account_id``: ``trajectory.request.provider_account_id``, the account at the provider.
- ``datadog_project_id``: ``trajectory.request.project_id``, the provider's project or workspace.

For example, in the proxy's ``config.yaml``::

    model_list:
      - model_name: gpt-4o
        litellm_params:
          model: openai/gpt-4o
          api_key: os.environ/OPENAI_API_KEY
        model_info:
          datadog_api_key_id: key_AbC123dEf456
          datadog_project_id: proj_AbC123dEf456

A value must be a string of 1 to 128 characters.

.. py:data:: ddtrace.config.litellm["usage_metrics_client_source"]

   The name of the client application using this proxy, sent as ``trajectory.client_source`` when set.

   Alternatively, set this option with the ``DD_LITELLM_USAGE_METRICS_CLIENT_SOURCE`` environment variable.

   Default: ``None``
"""  # noqa: E501
