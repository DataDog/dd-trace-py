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
handled each request, and how much usage LiteLLM reported**. It sends
DogStatsD usage counters tagged with user IDs and provider details,
but no prompt or response text. Collection is off until you register the
callback below; upgrading ``ddtrace`` alone does not enable it.

Quick setup
^^^^^^^^^^^

1. Install ``ddtrace`` in the same Python environment or container image as your
   LiteLLM proxy. The full gateway flow is tested with LiteLLM 1.101.0; older
   versions may not provide all the request hooks this feature needs.
2. Add the callback below to your existing LiteLLM configuration. Keep your
   existing callbacks, model settings, and provider credentials.

.. code-block:: yaml

    litellm_settings:
      callbacks:
        - ddtrace.contrib.litellm.gateway_attribution

3. Set the provider's **non-secret key ID** in each model's existing
   ``model_info``, including fallback models:

.. code-block:: yaml

    model_info:
      datadog_provider_api_key_id: key_abc123

Use the provider's key ID, such as OpenAI's ``key_...`` or Anthropic's
``apikey_...``. **Do not use the secret API key**, a masked key, or a LiteLLM
virtual key. Update the ID when you change keys. If a model uses different keys
per request, use separate model entries per key instead of a fixed ID.

If the ID is missing or invalid, the tracer logs a warning and still collects
usage without it. Repeated warnings are rate-limited. Providers without a key ID
can leave this field unset.

4. Enable the Agent's DogStatsD listener and start the gateway. Replace the paths
   and Agent address with your own. ``localhost`` works only if the Agent is reachable
   there from the gateway process.

.. code-block:: bash

    export DD_SERVICE=ai-gateway
    export DD_DOGSTATSD_URL=udp://localhost:8125
    litellm --config /etc/litellm/config.yaml

Use ``unix:///path/to/dsd.socket`` instead for a DogStatsD Unix socket. APM and
LLM Observability are not required. Keep ``ddtrace-run`` if you also want your
existing SDK tracing; its settings are separate from these metrics.

5. Check how your gateway identifies users; see below. Existing user settings
   are reused, so there is no separate Datadog user list to configure.

Without further configuration, the callback collects available user IDs and
authenticated email, usage, and the provider/model details LiteLLM makes
available. No extra configuration file is required to enable collection. To turn
it off, remove this callback and restart the gateway.

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

Send a normal request, then find ``ai_gateway.requests`` in Metrics Explorer.
Group by ``usr.id`` or ``ai.gateway.deployment_id`` to check attribution. Use
``ai.attribution.issues`` to see what is missing. For example,
``authenticated_user_unknown`` means no authenticated user ID was available.
Use ``.as_count()`` when summing these DogStatsD counters over time.

**These are custom metrics.** Each metric and tag combination creates a time
series; per-user tags can increase your custom-metric usage and bill. Unique
request/response IDs are deliberately omitted. Metric tags use Datadog's normal
character/case normalization and 200-character limit (including the key), so
email punctuation and long identifiers may change or be truncated. They are not
lossless copies of the original values. See the
`custom metrics guide <https://docs.datadoghq.com/metrics/custom_metrics/>`_ and
`tag rules <https://docs.datadoghq.com/getting_started/tagging/>`_.

Field reference
^^^^^^^^^^^^^^^

Fields are included only when available. Provider names, pricing settings, and
response traffic types are accepted as reported, including unfamiliar values;
metric tag normalization still applies.
Existing route details and actual outgoing settings take priority over LiteLLM's
standard logging payload. The payload fills missing common route details and
cache status; the callback's ``cache_hit`` value takes priority. Streaming is
flagged when either source reports it. Provider-specific fields and headers
remain explicitly selected; the full logging payload is never exported.

.. list-table:: Exported data
   :header-rows: 1
   :widths: 35 65

   * - Fields
     - Meaning
   * - Metric timestamps
     - Agent collection time, not individual request start/end times.
   * - ``usr.id``, ``usr.email``, ``team.id``, ``ai.gateway.org_id``
     - User ID (authenticated first, then the optional end-user fallback), plus
       authenticated team and gateway organization. A gateway organization is
       not a provider billing account. Authenticated email is included when
       available as ``usr.email``. Optional extra user fields use ``ai.enrichment.*``.
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
       tenancy/compartment. ARNs are sent without extracting an account or
       region, subject to metric tag limits. Provider ``model_id`` is not the gateway
       deployment ID.
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
   * - ``ai.gateway.deployment_id``
     - Selected gateway route ID. Individual request/response IDs are not exported.
   * - ``ai.route.api_key_id``
     - Provider key ID set in the selected model's
       ``model_info.datadog_provider_api_key_id``. Never read from client request
       metadata or discovered automatically. Missing or invalid IDs produce a warning.
   * - ``ai.response.openai_organization``, ``ai.response.openai_project``,
       ``ai.response.anthropic_organization_id``, ``ai.response.anthropic_workspace_id``
     - Organization, project, and workspace IDs returned in provider response
       headers, when LiteLLM keeps them. They stay separate from outgoing route
       settings and the gateway organization. Compatible proxies can return these
       headers too; their names alone do not prove which company bills the request.
       Other response headers are not exported.
   * - ``ai.observed.traffic_type``, ``ai.observed.service_tier``,
       ``ai.observed.speed``, ``ai.observed.inference_geo``
     - Pricing-related values reported in the response, kept separately from
       requested settings. Availability varies by provider and streaming behavior.
   * - ``ai_gateway.usage.*_tokens``
     - Non-overlapping token counts: input not served from cache, cache reads,
       cache writes with 5-minute/1-hour/unknown lifetimes, and output. Missing
       usage stays unknown, not zero. Missing cache details prevent calculating
       input not served from cache, except for embeddings.
   * - ``ai_gateway.usage.web_search_requests``, ``ai_gateway.usage.tool_search_requests``,
       ``ai_gateway.usage.browser_open_requests``, ``ai_gateway.usage.google_maps_grounding_requests``
     - Reported tool request counts, with duplicates removed and conflicts
       flagged. These are requests, not tokens.
   * - ``ai_gateway.observed.*`` counters
     - Input totals including caches (``context_tokens``), reasoning output,
       text/audio/image/video tokens, cache reads/writes by lifetime, prediction/
       tool tokens, character/image counts, and audio/video seconds, including
       fractions. These counts can overlap: **do not add them to** ``ai_gateway.usage.*``.
   * - ``ai.context_tokens.bucket``
     - Input length, including cached tokens, grouped at 32,000, 128,000, 200,000,
       256,000, 272,000, and 512,000 tokens. The same buckets apply to every model
       and provider; output tokens are excluded. Ranges have inclusive ends
       (for example, ``32001_128000``); above the largest boundary is ``512001_plus``.
       Missing or invalid usage, failures, and gateway cache hits use ``unknown``.
       This produces at most eight values, not a separate value for every token count.
   * - ``ai_gateway.observed.input_cache_read_reported``, ``ai_gateway.observed.input_cache_write_reported``
     - Number of responses for which LiteLLM supplied each counter. A reported
       zero is different from a missing field. This cannot recover data LiteLLM dropped
       or filled in before calling us.
   * - ``ai_gateway.requests``
     - Completed or incomplete logical requests, grouped by outcome and attribution.
   * - ``ai_gateway.observed.attempts``, ``ai_gateway.observed.retries``,
       ``ai_gateway.observed.fallbacks``
     - Selected Router attempts, retries, and entries into fallback routes observed
       by the deployment hook. Retry/fallback counters are omitted when LiteLLM's
       markers are unavailable. They do not include hidden HTTP/SDK retries or
       recover token usage from failed attempts. Counts are grouped under the
       final route, not allocated to each provider involved.
   * - ``ai.attribution.status``, ``ai.attribution.issues``, ``ai.usage.source``
     - Whether collection is incomplete, why, and where usage came from.
       ``observed`` means collected, **not independently verified**.

Limitations and privacy
^^^^^^^^^^^^^^^^^^^^^^^

* Supported requests: chat/text completions, Anthropic Messages, OpenAI Responses,
  and embeddings through the proxy. Standalone LiteLLM SDK calls, image generation,
  speech/transcription, video, batch jobs, and their later status updates are not covered. Available media counters
  within supported requests are still collected.
* Usage comes from LiteLLM, not directly from a billing record. Streaming is
  recorded when its final callback arrives; the callback does not buffer the
  stream. LiteLLM may estimate streaming usage, which is marked as unverified.
* Retries and fallbacks keep the final route's provider details. Earlier attempts
  may have missing usage. Failed/canceled requests do not mean zero cost.
  Gateway cache hits do not add new provider usage.
* The callback does not inspect credential files or guess billing IDs from a
  secret's format. Missing outgoing settings are not filled with request
  settings and presented as provider values.
* Mixed-media counts remain available, but are not split into non-overlapping
  categories when that split is unknown. Unknown cache-write lifetime is not
  assumed to be five minutes.
* Pending requests are held in memory: up to 10,000, with a one-hour expiry
  checked when new requests arrive. Expiry, eviction, and normal shutdown emit
  incomplete records. Forked workers discard inherited requests. Crashes or
  missing callbacks can lose data.
* Metrics are independent of APM sampling. DogStatsD delivery is best-effort,
  not a durable billing ledger; network loss or gateway crashes can lose counts.
  No dollar costs, streaming-speed measurements, or per-attempt token estimates
  are emitted.
* End-user IDs can contain personal information, including email, even with
  ``capture_email=false``. Client-supplied IDs can be wrong or change per request.
  The callback does not verify identity or change gateway access decisions.
* Collection is limited to the fields described above, with type, length, and
  secret checks. It does not filter valid values against a list of known names.
  This callback does not export prompts, response text, authorization headers,
  secret API keys, exception text, or arbitrary client metadata. Other integrations
  and LiteLLM's own logging have separate settings.

Optional: user data settings
^^^^^^^^^^^^^^^^^^^^^^^^^^^^

No extra configuration is needed unless you want to change which user details
are sent to Datadog.

.. envvar:: DD_LITELLM_GATEWAY_ATTRIBUTION_CONFIG

   Path to an optional JSON file. The callback reads it once at startup; restart
   the gateway after changing it. An invalid or unreadable file disables
   email, extra user metadata, and end-user capture, but does not stop the gateway.

Available user settings:

* ``capture_email``: ``true`` by default. Includes email from the gateway's
  authenticated user record as ``usr.email`` when available. Set to ``false``
  to omit this field.
* ``capture_end_user``: ``true`` by default. Set to ``false`` to disable collection
  of LiteLLM's end-user ID, including its use as a fallback for ``usr.id``.
* ``auth_metadata_keys``: empty by default. Select fields from
  ``UserAPIKeyAuth.metadata``, such as ``cost_center``, to include as
  ``ai.enrichment.cost_center``. This is normally virtual-key metadata or metadata
  returned by custom authentication, not automatically user or team profile metadata.
  Client-supplied request metadata is not used for these extra fields. Selection
  is explicit because this free-form data may contain secrets or unrelated
  personal information. User IDs and team IDs do not need this configuration.

For example, to turn off authenticated email collection, save this JSON in
``/etc/litellm/attribution.json`` and set
``DD_LITELLM_GATEWAY_ATTRIBUTION_CONFIG=/etc/litellm/attribution.json`` before
starting the gateway:

.. code-block:: json

    {"capture_email": false}

Only select user fields you intend to send to Datadog. User IDs can themselves
contain personal information, even when email collection is off. Values must be
non-empty strings without control characters or common secret prefixes. Most
identifiers are limited to 256 characters; resource IDs allow up to 2048.

Advanced: register from Python
^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^

Use ``ddtrace.contrib.litellm.GatewayAttribution()`` and add exactly one instance
to LiteLLM's callbacks. The optional user settings above are also accepted as
constructor arguments. Call ``close()`` if you manage shutdown yourself. The packaged ``gateway_attribution`` callback handles its own
process-exit cleanup.

"""  # noqa: E501
