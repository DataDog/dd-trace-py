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

Use this optional feature to see **who used your LiteLLM gateway, which model
handled each request, and how much usage LiteLLM reported**. It sends a Datadog
APM span named ``ai_gateway.usage``: a trace record with user IDs, usage counts,
and available billing details, but no prompt or response text.

The callback is code that LiteLLM calls during a request. It runs **inside the
gateway**, not on users' laptops. An Agent next to the gateway cannot collect
this information on its own. This feature does not require LLM Observability,
and ``ddtrace-run`` alone does not turn it on.

Quick setup
^^^^^^^^^^^

1. Install ``ddtrace`` in the same Python environment or container image as your
   LiteLLM proxy. The full gateway flow is tested with LiteLLM 1.101.0; older
   versions may not provide all the request hooks this feature needs.
2. Add the callback below to your LiteLLM configuration. Keep any existing
   callbacks. The model and provider key shown here are examples; keep your
   gateway's existing model settings.

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

3. Set the Agent address and start the gateway. Replace the paths and Agent
   address with your own. ``localhost`` works only if the Agent is reachable
   there from the gateway process.

.. code-block:: bash

    export DD_SERVICE=ai-gateway
    export DD_TRACE_AGENT_URL=http://localhost:8126
    ddtrace-run litellm --config /etc/litellm/config.yaml

4. Make sure your gateway authentication sets LiteLLM's ``user_id``. A shared
   credential identifies its owner or service, **not each person using it**.
   User IDs supplied in request bodies, headers, or client metadata are not
   trusted for attribution.

Without further configuration, the callback collects authenticated user IDs,
usage, and the provider/model details LiteLLM makes available. It leaves missing
billing details unknown rather than guessing.

Optional: add billing details
^^^^^^^^^^^^^^^^^^^^^^^^^^^^^

LiteLLM does not always expose the account, project, or key ID used on your bill.
Use a JSON file to fill in those details. This file contains **IDs, not secrets**.
It does not change where requests are routed.

.. envvar:: DD_LITELLM_GATEWAY_ATTRIBUTION_CONFIG

   Path to the JSON file. The callback reads it once at startup. Restart the
   gateway after changing it. If the file is invalid or unreadable, the gateway
   keeps working, but the callback ignores its billing and optional user settings.

For the YAML example above, save this as ``/etc/litellm/attribution.json`` and
replace the example billing IDs with your own:

.. code-block:: json

    {
      "billing_scopes": {
        "openai-coding-deployment": {
          "provider": "openai",
          "account_id": "org-example",
          "product": "api",
          "project_id": "proj-example"
        }
      },
      "capture_email": false,
      "auth_metadata_keys": ["cost_center"]
    }

Set the path **before starting the gateway**:

.. code-block:: bash

    export DD_LITELLM_GATEWAY_ATTRIBUTION_CONFIG=/etc/litellm/attribution.json
    ddtrace-run litellm --config /etc/litellm/config.yaml

The name ``openai-coding-deployment`` must match ``model_info.id`` in the YAML,
**not** the model alias ``coding-model``. Each deployment is a configured route
to a provider. Add a mapping for each route you want to identify, including
fallbacks, and update it when its credentials or cloud resources change.

.. list-table:: Settings in the JSON file
   :header-rows: 1
   :widths: 30 70

   * - Setting
     - What to enter
   * - ``billing_scopes``
     - Billing details by deployment ID. Optional; omit it if you only want user
       and usage data. Within each entry, ``provider``, ``account_id``, and
       ``product`` are required. For example: ``openai``, ``org-example``, ``api``.
   * - ``project_id``, ``resource_id``, ``api_key_id``
     - Optional fields within a billing entry. Use IDs from your provider.
       ``api_key_id`` is the provider's non-secret key ID, **never the API key
       itself or a LiteLLM virtual-key hash**.
   * - ``geography``, ``mode``, ``model``
     - Optional fields within a billing entry. Use the billing region, processing
       mode, or model name from your bill. Leave unknown values out: do not assume
       ``global`` or ``standard``. The original response model is kept separately.
       A mode reported in the response takes priority over a configured mode.
   * - ``capture_email``
     - ``false`` by default. Set to ``true`` to include email from the gateway's
       authenticated user record, when available, as ``usr.email``.
   * - ``auth_metadata_keys``
     - Empty by default. Names of fields to copy from authenticated user metadata,
       such as ``cost_center``. These appear as ``ai.enrichment.cost_center``.
       The callback does not read them from client-supplied request metadata.

Only select user fields you intend to send to Datadog. User IDs can themselves
contain personal information, even when email collection is off. Values must be
non-empty strings without control characters or common secret prefixes. Most
identifiers are limited to 256 characters; resource IDs allow up to 2048.

Check that it works
^^^^^^^^^^^^^^^^^^^

Send a normal request through the gateway, then look in APM for your service's
``ai_gateway.usage`` spans. Check ``usr.id``, ``ai.gateway.deployment_id``, and
``ai.billing.*``. Use ``ai.attribution.issues`` to see what is missing. For example,
``authenticated_user_unknown`` means no authenticated user ID was available;
``billing_scope_unknown`` means provider, billing account, or product is missing.
Sampling and ingestion settings can prevent individual spans from appearing.

Field reference
^^^^^^^^^^^^^^^

Fields are included only when available. The callback recognizes OpenAI,
Anthropic, Azure/Foundry, Bedrock, Vertex AI, Gemini, and OCI routes. A custom
OpenAI-compatible endpoint is **not** assumed to bill through OpenAI.

.. list-table:: Exported data
   :header-rows: 1
   :widths: 35 65

   * - Fields
     - Meaning
   * - Span start/duration; ``ai.timezone``
     - Request start and finish times, in UTC. Keep these when grouping usage
       into a provider's billing periods.
   * - ``usr.id``, ``team.id``, ``ai.gateway.org_id``
     - Authenticated user, team, and gateway organization. A gateway organization
       is not a provider billing account. Optional email and extra user fields
       use ``usr.email`` and ``ai.enrichment.*``.
   * - ``ai.billing.*``
     - Provider, account, product, and optional project/resource/key IDs,
       geography, and mode. Your mappings override route defaults. Explicit
       OpenAI organization/project, Vertex project, Bedrock resource ARN, and
       OCI compartment can be collected automatically. A project or resource
       owner's account is not assumed to be the billed account.
   * - ``ai.billing.provider_source``, ``ai.billing.mode_source``
     - Where the value came from. For mode, a returned Vertex/Gemini traffic type
       takes priority over a returned service tier, then the configured mode.
       Conflicting on-demand response values are flagged as incomplete.
   * - ``ai.model``, ``ai.model.source``, ``ai.response.model``
     - Model used for billing comparisons, where it came from, and the original
       response model. Without a mapping, use the response model, or the selected
       route model if the response has none. Model version suffixes are kept.
   * - ``ai.route.*``
     - Selected provider/model, endpoint hostname, region/location, and API
       version. Also includes available OpenAI organization/project, Vertex
       project, Bedrock project/resource ARN/owner account, and OCI tenancy/
       compartment. Bedrock's provider ``model_id`` is not the gateway deployment
       ID. Actual outgoing endpoint and OpenAI scope headers take priority over
       route defaults. Region or resource ownership alone does not prove billing
       geography or account. URLs' paths, queries, and credentials are not copied.
   * - ``ai.request.*``, ``ai.effective.*``
     - Settings received by the gateway versus those sent to the provider:
       service tier, speed, reasoning/thinking budgets, image quality/size,
       inference geography, cache retention, output limits/counts, embedding
       dimensions, search context, and Bedrock performance latency. These are
       settings, not proof of usage or the price charged.
   * - ``ai.request.prompt_cache_ttls``, ``ai.effective.prompt_cache_ttls``
     - Provider prompt-cache lifetimes, not the gateway's response-cache lifetime.
       An explicit cache-control block without a lifetime uses the five-minute
       default. This does not tell us how many tokens were written at each
       lifetime. Large payloads can produce ``prompt_cache_scan:incomplete``.
   * - ``ai.gateway.deployment_id``, ``ai.request.id``, ``ai.response.id``
     - Selected route ID, generated gateway request ID, and provider response ID.
       These help find requests but may not exist in the provider's bill.
   * - ``ai.response.x_request_id``, ``ai.response.request_id``,
       ``ai.response.x_amzn_requestid``, ``ai.response.apim_request_id``,
       ``ai.response.opc_request_id``
     - Request IDs from selected provider response headers, when LiteLLM keeps
       them. Other response headers are not exported. Missing IDs stay missing.
   * - ``ai.observed.traffic_type``, ``ai.observed.service_tier``,
       ``ai.observed.speed``, ``ai.observed.inference_geo``
     - Pricing-related values reported in the response, kept separately from
       requested settings. Availability varies by provider and streaming behavior.
   * - ``ai.usage.*_tokens``
     - Non-overlapping token counts: input not served from cache, cache reads,
       cache writes with 5-minute/1-hour/unknown lifetimes, and output. Missing
       usage stays unknown, not zero. Missing cache details prevent calculating
       input not served from cache, except for embeddings.
   * - ``ai.usage.web_search_requests``, ``ai.usage.tool_search_requests``,
       ``ai.usage.browser_open_requests``, ``ai.usage.google_maps_grounding_requests``
     - Reported tool request counts, with duplicates removed and conflicts
       flagged. These are requests, not tokens.
   * - ``ai.observed.*`` usage counts
     - Input totals including caches (``context_tokens``), reasoning output,
       text/audio/image/video tokens, cache reads/writes by lifetime, prediction/
       tool tokens, character/image counts, and audio/video seconds, including
       fractions. These counts can overlap: **do not add them to** ``ai.usage.*``.
   * - ``ai.observed.input_cache_read_reported``, ``ai.observed.input_cache_write_reported``
     - ``1`` if LiteLLM supplied the counter, ``0`` if not. A reported zero is
       different from a missing field. This cannot recover data LiteLLM dropped
       or filled in before calling us.
   * - ``ai.attribution.status``, ``ai.attribution.issues``, ``ai.usage.source``
     - Whether collection is incomplete, why, and where usage came from.
       ``observed`` means collected, **not verified against an invoice**.

Using the data with costs
^^^^^^^^^^^^^^^^^^^^^^^^^

This callback collects inputs for a cost join; it does **not** perform the join
or calculate an invoice. Match usage to each provider's billing data using:

* Time period, billing provider/account/product, and model.
* Project, resource, or non-secret key ID when the bill uses that level of detail.
  Account-wide allocation does not require every optional ID.
* Usage category and amount. Metric names include units such as tokens, requests,
  counts, or seconds. Do not add overlapping observed counts to usage totals.
* Processing mode and billing geography, using reported values and mappings.
  Missing values stay unknown, not ``standard`` or ``global``.

If pricing depends on request size, use each request's ``context_tokens`` and
that provider's rules **before adding requests together**. Session size is not
request size. Billing SKU lookup happens on the cost side, not in the gateway.

Only allocate the part of a bill that the collected usage covers. If requests
are missing, leave some cost unattributed rather than assigning the whole bill
to the users you can see. Credits, fees, seats, and reserved capacity need their
own allocation rules, not token counts.

Limitations and privacy
^^^^^^^^^^^^^^^^^^^^^^^

* Supported requests: chat/text completions, Anthropic Messages, OpenAI Responses,
  and embeddings. Standalone image generation, speech/transcription, video, batch
  jobs, and their later status updates are not covered. Available media counters
  within supported requests are still collected.
* Usage comes from LiteLLM, not directly from a billing record. Streaming is
  recorded when its final callback arrives; the callback does not buffer the
  stream. LiteLLM may estimate streaming usage, which is marked as unverified.
* Retries and fallbacks keep the final route's billing details. Earlier attempts
  may have missing usage. Failed/canceled requests do not mean zero cost.
  Gateway cache hits do not add new provider usage.
* Client-provided credentials or endpoint changes disable the configured billing
  mapping for that request. Missing account/key IDs need your mapping; the
  callback does not read credential files to discover them. Missing outgoing
  settings are not filled with request settings and presented as provider values.
* Mixed-media counts remain available, but are not split into non-overlapping
  categories when that split is unknown. Unknown cache-write lifetime is not
  assumed to be five minutes.
* Pending requests are held in memory: up to 10,000, with a one-hour expiry
  checked when new requests arrive. Expiry, eviction, and normal shutdown emit
  incomplete records. Forked workers discard inherited requests. Crashes or
  missing callbacks can lose data; this is not a permanent billing ledger.
* APM sampling, delivery, and retention rules still apply. Sampled traces are
  not a complete usage total. Do not count the same usage again from SDK spans.
* This callback does not export prompts, response text, authorization headers,
  API keys, exception text, or arbitrary client metadata. Other integrations
  and LiteLLM's own logging have separate settings.

Advanced: register from Python
^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^

Use ``ddtrace.contrib.litellm.GatewayAttribution(billing_scopes=...,
capture_email=False, auth_metadata_keys=())`` and add exactly one instance to
LiteLLM's callbacks. Call ``close()`` before ``tracer.shutdown()`` if you manage
shutdown yourself. The packaged ``gateway_attribution`` callback handles its own
process-exit cleanup.

"""  # noqa: E501
