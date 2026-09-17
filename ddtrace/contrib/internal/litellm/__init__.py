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
identity with response usage and operator-supplied billing dimensions. It runs
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
   response usage, but billing scope remains unknown. An invalid file disables
   billing and optional identity enrichment without blocking gateway requests.

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
Do not allowlist secrets or sensitive attributes you do not intend to export.
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
     - Optional authenticated email and allowlisted authenticated metadata.
   * - ``ai.billing.provider``, ``ai.billing.account_id``, ``ai.billing.product``
     - Operator-maintained billing source, account, and product for the selected
       deployment. The billing provider need not be the model manufacturer.
   * - ``ai.billing.project_id``, ``ai.billing.resource_id``, ``ai.billing.api_key_id``
     - Optional project/workspace, cloud resource, and non-secret provider key ID.
   * - ``ai.billing.geography``, ``ai.billing.mode``
     - Configured billing geography and processing mode. A response-resolved
       ``service_tier`` overrides configured mode; requested tiers, execution
       location, and an ``auto`` tier are not inferred billing evidence.
   * - ``ai.model``, ``ai.model.source``, ``ai.response.model``
     - Billing-model mapping or raw response model, provenance, and raw response
       model including version/pricing suffixes.
   * - ``ai.gateway.deployment_id``, ``ai.request.id``, ``ai.response.id``
     - Selected deployment, generated logical request ID, and response ID when
       provided. IDs do not imply that billing exports support request-level joins.
   * - ``ai.usage.*_tokens``
     - Disjoint uncached input, cache-read input, cache-write input by 5-minute,
       1-hour, or unknown TTL, and output. Missing usage is not replaced by zero.
   * - ``ai.usage.web_search_requests``, ``ai.usage.tool_search_requests``
     - Tool request counts, only when present in response usage. Units are requests,
       not tokens; not every gateway/provider response exposes them.
   * - ``ai.observed.context_tokens``, ``ai.observed.reasoning_output_tokens``
     - Per-request input including caches, and reasoning as a subset of output.
       These diagnostics must not be added to the disjoint usage quantities.
   * - ``ai.attribution.status``, ``ai.attribution.issues``, ``ai.usage.source``
     - Collection completeness, missing/ambiguous dimensions, and usage provenance.
       ``observed`` means dimensions were collected, not invoice-exact billing.

Coverage and limitations
^^^^^^^^^^^^^^^^^^^^^^^^

* Chat/text completions, native Anthropic Messages, and OpenAI Responses are
  observed at the logical gateway request boundary. Streaming uses the completed
  callback only; the callback neither consumes nor buffers the stream.
* Usage is LiteLLM-normalized, not a preserved raw provider billing record.
  LiteLLM can reconstruct streaming usage; streaming spans are additionally marked
  with unverified provenance rather than claiming every count came from the provider.
* Retries/fallbacks retain the final deployment's scope and flag potentially
  missing earlier-attempt usage. Failed, cancelled, or missing callbacks do not
  imply zero billable usage. Gateway cache hits emit no new provider quantities.
* Client-supplied credentials or endpoint overrides suppress configured billing
  scope. Multimodal usage is diagnostic-only when disjoint categories cannot be
  established. Unknown cache-write TTL is never assumed to be five minutes.
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
