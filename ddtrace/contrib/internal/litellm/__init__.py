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
They are recorded for every call, whether or not a trace is sent, and contain no prompts or responses.

For each request from the proxy to a model provider, including each retry and fallback:

- ``gen_ai.client.inference.duration`` (``gen_ai.client.operation.duration`` for embeddings), with ``error.type`` on failures.
- ``gen_ai.client.inference.usage.input_tokens`` and ``gen_ai.client.inference.usage.output_tokens``. Input tokens include
  cached input. A count the provider did not report is tagged ``trajectory.token.source:estimated``.
- ``gen_ai.client.inference.usage.cache_read.input_tokens`` and ``gen_ai.client.inference.usage.cache_write.input_tokens``.
- ``trajectory.gen_ai.client.inference.usage.cost``: the proxy's own price for the call, tagged
  ``trajectory.cost.source:estimated``.

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
   headers in ``OTEL_EXPORTER_OTLP_METRICS_HEADERS``. Without an endpoint, metrics go to the Datadog Agent's OTLP receiver
   on port 4318. ``dogstatsd`` sends the same series to the Datadog Agent's DogStatsD endpoint.

   Alternatively, set this option with the ``DD_LITELLM_USAGE_METRICS_EXPORTER`` environment variable.

   Default: ``otlp``

.. py:data:: ddtrace.config.litellm["usage_metrics_tags"]

   A comma-separated list of the optional tags to add. Each can raise the number of series.

   - ``user``: ``user.id``, the user who owns the proxy key.
   - ``team``: ``trajectory.team.id``, the team of the proxy key.
   - ``key_alias``: ``trajectory.gateway.key.alias``, the alias of the proxy key. Keys and key hashes are never sent.
   - ``route``: ``trajectory.gateway.route``, the model group the client asked for.
   - ``destination``: ``trajectory.gateway.destination.id``, the deployment the proxy chose.
   - ``service``, ``host``: ``service.name`` and ``host.name`` of this process.

   Alternatively, set this option with the ``DD_LITELLM_USAGE_METRICS_TAGS`` environment variable.

   Default: ``""``

.. py:data:: ddtrace.config.litellm["usage_metrics_client_source"]

   The name of the client application using this proxy, sent as ``trajectory.client_source`` when set.

   Alternatively, set this option with the ``DD_LITELLM_USAGE_METRICS_CLIENT_SOURCE`` environment variable.

   Default: ``None``
"""  # noqa: E501
