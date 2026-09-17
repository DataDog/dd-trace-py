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


Gateway usage attribution
~~~~~~~~~~~~~~~~~~~~~~~~~

For a customer-operated LiteLLM proxy, the optional gateway callback records
content-free ``ai_gateway.usage`` APM spans. It combines authenticated gateway
identity with response usage, selected-route metadata and optional billing mappings. It runs
inside the gateway process: installing an Agent alongside the gateway alone
cannot recover authenticated identity from encrypted upstream traffic.

The callback is separate from ordinary LiteLLM SDK/LLM Observability tracing.
It does not require LLM Observability or change its collection settings. The
callback path is opt-in; ``ddtrace-run`` alone does not enable it.

Installation and configuration
^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^

Install ``ddtrace`` in the same Python environment or container image as the
LiteLLM proxy. Configure the usual Datadog Agent connection and unified service
tags, then start the proxy with ``ddtrace-run litellm --config /etc/litellm/config.yaml``.
The proxy callback contract is exercised with LiteLLM 1.101.0; older proxy
versions may not invoke all the hooks used for attribution.

Add the packaged callback to the gateway configuration. No custom Python adapter
or separate attribution package is necessary:

.. code-block:: yaml

    model_list:
      - model_name: coding-model
        litellm_params:
          model: openai/gpt-4o
          api_key: os.environ/OPENAI_API_KEY
        model_info:
          id: openai-coding-deployment
    litellm_settings:
      callbacks:
        - ddtrace.contrib.litellm.gateway_attribution

Use a gateway authentication mechanism that populates LiteLLM's authenticated
``user_id``. A shared credential identifies its owner or service, not every human
using it. Request ``user`` fields, arbitrary headers, and client metadata are
not trusted sources of identity.

.. envvar:: DD_LITELLM_GATEWAY_ATTRIBUTION_CONFIG

   Optional path to an operator-controlled JSON file, read once when the callback
   is loaded. Without it, the callback still collects authenticated user IDs and
   response usage, selected-route dimensions, and selected pricing settings.
   Billing dimensions that cannot be established from the route remain unknown.
   An invalid file disables operator mappings and optional identity enrichment
   without blocking gateway requests.

   Example:

   .. code-block:: json

       {
         "billing_scopes": {
           "openai-coding-deployment": {
             "provider": "openai",
             "account_id": "org-example",
             "product": "api",
             "project_id": "proj-example",
             "api_key_id": "key-id-example",
             "geography": "global",
             "mode": "standard"
           }
         },
         "capture_email": false,
         "auth_metadata_keys": ["cost_center"]
       }

``billing_scopes`` keys must match the **selected deployment's** ``model_info.id``,
not its user-facing model alias. Define a scope for each deployment, including
fallbacks. Keep it updated when credentials, cloud resources, or routes change.
``provider``, ``account_id``, and ``product`` are required within a configured scope.
Other billing fields are optional; omit unknown values rather than guessing.
``api_key_id`` means a non-secret provider key identifier, never the API key value
or the gateway's virtual-key hash. ``model`` optionally maps the response model
onto a provider billing model; the raw response model is always retained separately.

``capture_email`` defaults to false. ``auth_metadata_keys`` defaults to an empty
list and copies only explicitly named attributes from the authenticated principal.
Do not select secrets or sensitive attributes you do not intend to export.
Only non-empty string identifiers up to 256 characters are copied; containers,
control characters, and common secret prefixes are rejected. Authenticated IDs
themselves can contain personal information, including email.

For programmatic registration, use
``ddtrace.contrib.litellm.GatewayAttribution(billing_scopes=..., capture_email=False,
auth_metadata_keys=())`` and register exactly one instance in LiteLLM's callbacks.
Call ``close()`` before ``tracer.shutdown()`` during a managed shutdown. The
packaged ``gateway_attribution`` instance registers its own process-exit cleanup.

Collected dimensions
^^^^^^^^^^^^^^^^^^^^

.. list-table:: Usage and attribution fields
   :header-rows: 1
   :widths: 35 65

   * - Field
     - Source and meaning
   * - Span start/duration; ``ai.timezone``
     - Gateway ingress time and completed callback time; UTC. Preserve these
       before aggregation into provider billing intervals.
   * - ``usr.id``, ``team.id``, ``ai.gateway.org_id``
     - Authenticated gateway principal, team, and organization where available.
       The gateway organization is **not** a provider billing account.
   * - ``usr.email``, ``ai.enrichment.<key>``
     - Optional authenticated email and explicitly selected authenticated metadata.
   * - ``ai.billing.provider``, ``ai.billing.account_id``, ``ai.billing.product``
     - Provider and product inferred for recognized OpenAI, Anthropic, Azure,
       Bedrock, Vertex AI, and Gemini routes using known endpoints or adapter
       defaults, including recognized OpenAI regional endpoints. Explicit OpenAI
       organization is also collected. Operator mappings
       override these values; ``ai.billing.provider_source`` records provenance.
       An arbitrary OpenAI-compatible endpoint does not imply OpenAI billing.
   * - ``ai.billing.project_id``, ``ai.billing.resource_id``, ``ai.billing.api_key_id``
     - Optional configured project/workspace, cloud resource, and non-secret
       provider key ID. Explicit Vertex AI project and OpenAI project from outgoing
       ``OpenAI-Project`` headers are collected automatically;
       it is not substituted for the GCP billing account.
   * - ``ai.billing.geography``, ``ai.billing.mode``
     - Configured billing geography and processing mode. A response-resolved
       ``service_tier`` overrides configured mode; requested tiers, execution
       location, and an ``auto`` tier are not inferred billing evidence.
   * - ``ai.model``, ``ai.model.source``, ``ai.response.model``
     - Billing-model mapping or raw response model, provenance, and raw response
       model including version/pricing suffixes. Selected route model is a
       fallback when the response omits its model.
   * - ``ai.route.*``
     - Selected provider/model, endpoint hostname only, OpenAI endpoint region,
       OpenAI organization/project,
       Vertex project/location, AWS region/Bedrock project, region name and API
       version, where exposed. Outgoing provider endpoints and non-secret OpenAI
       scope headers take precedence over route defaults. Authorization and other
       headers, URL paths, queries, and user information are not retained.
       Endpoint residency and execution location are not assumed to be billed geography.
   * - ``ai.request.*``, ``ai.effective.*`` pricing settings
     - Selected service tier, speed, reasoning effort, image quality/size,
       inference geography, prompt-cache retention, number of outputs, embedding
       dimensions, thinking budgets, search context size, Bedrock performance
       latency and output limits. Ingress settings and outgoing provider-payload
       settings are separate; neither implies a returned quantity or billed mode.
   * - ``ai.request.prompt_cache_ttls``, ``ai.effective.prompt_cache_ttls``
     - Cache lifetimes found at ingress and in the outgoing provider payload,
       including the five-minute default for an explicit cache-control block.
       This is provider prompt caching, not the gateway response-cache lifetime.
       Mixed lifetimes do not establish token quantities for each lifetime. Structural scans are
       bounded and set ``prompt_cache_scan:incomplete`` if truncated.
   * - ``ai.gateway.deployment_id``, ``ai.request.id``, ``ai.response.id``
     - Selected deployment, generated logical request ID, and response ID when
       provided. IDs do not imply that billing exports support request-level joins.
   * - ``ai.usage.*_tokens``
     - Disjoint input not served from cache, cache-read input, cache-write input by
       5-minute, 1-hour, or unknown lifetime, and output. Missing usage is not replaced by zero.
   * - ``ai.usage.web_search_requests``, ``ai.usage.tool_search_requests``,
       ``ai.usage.browser_open_requests``, ``ai.usage.google_maps_grounding_requests``
     - Tool counts from response usage, without adding duplicate native and
       normalized counts. Conflicting values are flagged. Units are requests,
       not tokens; not every gateway/provider response exposes them.
   * - ``ai.observed.context_tokens``, ``ai.observed.reasoning_output_tokens``
     - Per-request input including caches, and reasoning as a subset of output.
       These diagnostics must not be added to the disjoint usage quantities.
   * - ``ai.observed.input_*``, ``ai.observed.output_*`` and tool counters
     - Explicit text/audio/image/video tokens, cached and cache-write tokens,
       writes for each cache lifetime, reasoning/prediction/tool tokens, character/image counts,
       and audio/video duration in seconds when present in usage. Fractional
       seconds are preserved. These may overlap each other and ``ai.usage.*``;
       they remain available even when mixed-media allocation is ambiguous.
   * - ``ai.attribution.status``, ``ai.attribution.issues``, ``ai.usage.source``
     - Collection completeness, missing/ambiguous dimensions, and usage provenance.
       ``observed`` means dimensions were collected, not invoice-exact billing.

Cost-join inputs
^^^^^^^^^^^^^^^^

These fields retain inputs for provider-specific cost allocation, rather than
performing the join inside the gateway:

* ``usage_timestamp`` comes from span time, while ``billing_provider``,
  ``billing_account_id``, and ``billing_product`` use the corresponding
  ``ai.billing.*`` dimensions. Operator mappings are still needed when opaque
  credentials do not reveal the account or product.
* ``resource_scope`` uses project/workspace or resource identifiers, and
  ``api_key_id`` uses the configured non-secret provider key ID. They are only
  needed for billing slices scoped that way; account-wide allocation does not
  require every optional scope field. A Bedrock profile ARN remains intact in
  ``ai.route.model``; do not remove routing prefixes before extracting scope.
* ``model_id`` retains raw route/response models and optional billing mappings.
  ``usage_type`` and ``usage_amount`` are encoded together in named numeric
  counters, with tokens, requests, counts or seconds in the metric name. Do not
  add overlapping ``ai.observed.*`` diagnostics to ``ai.usage.*`` quantities.
* ``processing_mode`` and ``billing_geography`` can use configured billing fields
  together with response-resolved tier/speed/geography, outgoing settings and raw
  route/endpoint scope. Missing settings remain unknown, not standard or global.
* ``context_band`` can be classified downstream from each request's
  ``ai.observed.context_tokens`` (including caches), raw model and applicable
  provider pricing rules **before** aggregation. There is no universal threshold
  and session size is not a substitute for request context.
* ``billing_sku`` is a cost-side lookup, not a required gateway field. Map the
  observed dimensions to the provider's SKU or composite billing item downstream.

Usage coverage must match the billing slice, or allocation needs an authoritative
denominator and an unattributed remainder. Never distribute an entire shared-key
bill only among the users whose requests were observed. Credits, fees, seats and
provisioned capacity require separate allocation rules, not token weights.

Coverage and limitations
^^^^^^^^^^^^^^^^^^^^^^^^

* Chat/text completions, native Anthropic Messages, OpenAI Responses, and embeddings are
  observed at the logical gateway request boundary. Streaming uses the completed
  callback only; the callback neither consumes nor buffers the stream.
* Usage is LiteLLM-normalized, not a preserved raw provider billing record.
  LiteLLM can reconstruct streaming usage; streaming spans are additionally marked
  with unverified provenance rather than claiming every count came from the provider.
* Retries/fallbacks retain the final deployment's scope and flag potentially
  missing earlier-attempt usage. Failed, canceled, or missing callbacks do not
  imply zero billable usage. Gateway cache hits emit no new provider quantities.
* Client-supplied credentials or endpoint overrides suppress configured billing
  scope. Mixed-media usage is diagnostic-only when disjoint categories cannot be
  established. Unknown cache-write lifetime is never assumed to be five minutes.
* Opaque credentials do not reveal billing-account or non-secret provider-key IDs.
  Use operator mappings for these and for billing geography, custom endpoints,
  deployment classes, or products not established by the selected route. Outgoing
  settings depend on LiteLLM exposing its provider payload; absent fields are not
  copied from the ingress request and mislabeled as effective settings.
* Standalone image generation, speech/transcription, video, batch jobs, and
  asynchronous job lifecycle APIs are not covered by this callback. Modality
  counters on supported chat, Responses and embedding requests are collected.
* The callback retains bounded, process-local state: at most 10,000 requests, with
  a one-hour expiry checked on new requests. Eviction and graceful shutdown emit
  incomplete records. Forked workers discard inherited requests. Abrupt process
  termination, unmatched callbacks, or requests exceeding these limits can lose
  usage; this is not a durable billing ledger.
* APM sampling, Agent transport, and ingestion/retention rules still apply. Do not
  treat sampled span sums as complete spend, or combine these quantities with
  overlapping SDK spans. Cost reconciliation must use provider-specific billing
  intervals, categories, scope and pricing rules. This integration does not itself
  join costs, calculate an invoice, or allocate flat-rate/seat subscriptions.
* This callback exports no prompts, responses, authorization headers, raw keys,
  exception text, or arbitrary client metadata. Other installed integrations and
  LiteLLM logging have independent content-collection settings.

"""  # noqa: E501
