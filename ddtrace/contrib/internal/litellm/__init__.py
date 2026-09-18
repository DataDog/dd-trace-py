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
and provider details, but no prompt or response text.

Quick setup
^^^^^^^^^^^

1. Install ``ddtrace`` in the same Python environment or container image as your
   LiteLLM proxy. The full gateway flow is tested with LiteLLM 1.101.0; older
   versions may not provide all the request hooks this feature needs.
2. Add the callback below to your existing LiteLLM configuration. Keep your
   existing callbacks, model settings, and provider credentials. You do not need
   to enter API keys or model names again for this integration.

.. code-block:: yaml

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

4. Check how your gateway identifies users; see below. Existing user settings
   are reused, so there is no separate Datadog user list to configure.

Without further configuration, the callback collects available user IDs,
usage, and the provider/model details LiteLLM makes available. No billing
configuration file is required to enable collection.

How users are identified
^^^^^^^^^^^^^^^^^^^^^^^^

**Recommended: identify users through gateway authentication.** If you already
give each person a LiteLLM virtual key linked to their user account, no extra
setup is needed. When creating a key through ``/key/generate``, set
``"user_id": "employee-123"`` to link it to that user. For custom authentication,
validate the caller's credentials and return
``UserAPIKeyAuth(user_id=verified_user_id)``. See LiteLLM's
`virtual keys <https://docs.litellm.ai/docs/proxy/virtual_keys>`_ and
`custom authentication <https://docs.litellm.ai/docs/proxy/custom_auth>`_ guides.
Do not assume every key has a user: shared or service credentials may not identify
the person making the request.

**Fallback: use the end-user ID LiteLLM already collects.** For example, a client
can send ``"user": "employee-123"`` in an OpenAI-compatible request, or the header
``x-litellm-end-user-id: employee-123``. LiteLLM also supports metadata and
configured customer-ID headers; see its
`end-user guide <https://docs.litellm.ai/docs/proxy/customers>`_. No extra Datadog
mapping is needed. Supported sources depend on your LiteLLM version.

The callback uses the gateway's ``user_id`` as ``usr.id`` first. If it is missing,
it uses LiteLLM's ``end_user_id`` and sets ``ai.identity.source=litellm_end_user``.
The end-user ID is also kept as ``ai.end_user.id``, even when ``usr.id`` identifies
a shared service. It is always marked ``ai.end_user.trust=unverified``: a caller
may choose this value, and this callback cannot prove who supplied it. Prefer
authenticated identity for reliable cost attribution. If neither ID is available,
``usr.id`` is omitted.

Set ``capture_end_user`` to ``false`` in the optional user settings below to
collect only gateway-authenticated identity. IDs LiteLLM omits are not recovered
from raw request fields, and JSON objects containing device/session details are
not used as user IDs.

Check that it works
^^^^^^^^^^^^^^^^^^^

Send a normal request through the gateway, then look in APM for your service's
``ai_gateway.usage`` spans. Check ``usr.id``, ``ai.gateway.deployment_id``, and
``ai.route.*``. Use ``ai.attribution.issues`` to see what is missing. For example,
``authenticated_user_unknown`` means no authenticated user ID was available,
even if an unverified fallback was collected.
Sampling and ingestion settings can prevent individual spans from appearing.

Field reference
^^^^^^^^^^^^^^^

Fields are included only when available. Provider names, pricing settings, and
response traffic types are kept as reported, including unfamiliar values. The
callback does not translate them into billing providers, accounts, or modes.
Interpret these values on the cost side; for example, ``azure_ai`` stays
``azure_ai``, and ``ON_DEMAND_PRIORITY`` stays ``ON_DEMAND_PRIORITY``.

.. list-table:: Exported data
   :header-rows: 1
   :widths: 35 65

   * - Fields
     - Meaning
   * - Span start/duration; ``ai.timezone``
     - Request start and finish times, in UTC. Keep these when grouping usage
       into a provider's billing periods.
   * - ``usr.id``, ``team.id``, ``ai.gateway.org_id``
     - User ID (authenticated first, then the optional end-user fallback), plus
       authenticated team and gateway organization. A gateway organization is
       not a provider billing account. Optional authenticated email and extra
       user fields use ``usr.email`` and ``ai.enrichment.*``.
   * - ``ai.identity.source``, ``ai.end_user.id``, ``ai.end_user.trust``
     - ``usr.id`` comes from ``gateway_auth`` or ``litellm_end_user``; otherwise
       its source is ``unknown``. The separate end-user ID is always marked
       ``unverified`` and never overwrites an available authenticated ID.
   * - ``ai.model``, ``ai.model.source``, ``ai.response.model``
     - Response model, or the selected route model if the response has none;
       its source; and the original response model. Version suffixes are kept.
   * - ``ai.route.*``
     - Selected provider/model, endpoint hostname, region/location, and API
       version. Also includes available OpenAI organization/project, Vertex
       project, Bedrock project, provider ``model_id``/``resource_id``, and OCI
       tenancy/compartment. ARNs stay intact, without extracting an account or
       region. Provider ``model_id`` is not the gateway deployment ID.
       Actual outgoing endpoint and OpenAI scope headers take priority over
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
       Explicit lifetimes and cache types are kept as reported, including new
       values. Types use ``prompt_cache_types``; a block with no lifetime sets
       ``prompt_cache_ttl_unspecified:true`` instead of assuming a default.
       These do not count tokens at each lifetime. Large payloads can produce
       ``prompt_cache_scan:incomplete``.
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
or calculate an invoice. Resolve the raw route and response fields to the
provider's billing dimensions downstream. Automatic matching from credentials
to billing key/account IDs is not implemented, and API keys are never exported.
A cost join needs:

* Time period, billing provider/account/product, and model.
* Project, resource, or non-secret key ID when the bill uses that level of detail.
  Account-wide allocation does not require every optional ID.
* Usage category and amount. Metric names include units such as tokens, requests,
  counts, or seconds. Do not add overlapping observed counts to usage totals.
* Processing mode and billing geography, resolved using provider-specific rules.
  A requested tier or route location is not proof of what was billed.

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
* Retries and fallbacks keep the final route's provider details. Earlier attempts
  may have missing usage. Failed/canceled requests do not mean zero cost.
  Gateway cache hits do not add new provider usage.
* The callback does not inspect credential files or derive billing key/account
  IDs from secret API keys. Missing outgoing settings are not filled with request
  settings and presented as provider values.
* Mixed-media counts remain available, but are not split into non-overlapping
  categories when that split is unknown. Unknown cache-write lifetime is not
  assumed to be five minutes.
* Pending requests are held in memory: up to 10,000, with a one-hour expiry
  checked when new requests arrive. Expiry, eviction, and normal shutdown emit
  incomplete records. Forked workers discard inherited requests. Crashes or
  missing callbacks can lose data; this is not a permanent billing ledger.
* APM sampling, delivery, and retention rules still apply. Sampled traces are
  not a complete usage total. Do not count the same usage again from SDK spans.
* End-user IDs can contain personal information, including email, even with
  ``capture_email=false``. Client-supplied IDs can be wrong or change per request.
  The callback does not verify identity or change gateway access decisions.
* Collection is limited to the fields described above, with type, length, and
  secret checks. It does not filter valid values against a list of known names.
  This callback does not export prompts, response text, authorization headers,
  API keys, exception text, or arbitrary client metadata. Other integrations
  and LiteLLM's own logging have separate settings.

Optional: user data settings
^^^^^^^^^^^^^^^^^^^^^^^^^^^^

No extra configuration is needed unless you want to change which user details
are sent to Datadog.

.. envvar:: DD_LITELLM_GATEWAY_ATTRIBUTION_CONFIG

   Path to an optional JSON file. The callback reads it once at startup; restart
   the gateway after changing it. An invalid or unreadable file disables optional
   user enrichment and end-user capture, but does not stop the gateway.

Available user settings:

* ``capture_email``: ``false`` by default. Set to ``true`` to include email from
  the gateway's authenticated user record as ``usr.email``.
* ``capture_end_user``: ``true`` by default. Set to ``false`` to disable collection
  of LiteLLM's end-user ID, including its use as a fallback for ``usr.id``.
* ``auth_metadata_keys``: empty by default. Select authenticated user metadata
  fields such as ``cost_center`` to include as ``ai.enrichment.cost_center``.
  Client-supplied request metadata is not used for these extra fields.

For example, to include authenticated email, save this JSON in
``/etc/litellm/attribution.json`` and set
``DD_LITELLM_GATEWAY_ATTRIBUTION_CONFIG=/etc/litellm/attribution.json`` before
starting the gateway:

.. code-block:: json

    {"capture_email": true}

Only select user fields you intend to send to Datadog. User IDs can themselves
contain personal information, even when email collection is off. Values must be
non-empty strings without control characters or common secret prefixes. Most
identifiers are limited to 256 characters; resource IDs allow up to 2048.

Advanced: register from Python
^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^

Use ``ddtrace.contrib.litellm.GatewayAttribution()`` and add exactly one instance
to LiteLLM's callbacks. The optional user settings above are also accepted as
constructor arguments. Call ``close()`` before ``tracer.shutdown()`` if you manage
shutdown yourself. The packaged ``gateway_attribution`` callback handles its own
process-exit cleanup.

"""  # noqa: E501
