# Reference Integrations

Canonical dd-trace-py integrations organized by the 13 integration categories.
**Read the canonical integration for your category before writing code.**

All patch modules live in `ddtrace/contrib/internal/{name}/`.

## By Category

| Category | Canonical | Secondary | Notes |
|----------|-----------|-----------|-------|
| cache | `redis/patch.py` | `pymemcache/patch.py` | Key-value stores, `cache.*` span tags, uses Pin + `context_with_data` via redis_utils |
| cloud-provider | `botocore/patch.py` | `google_genai/patch.py` | AWS services via botocore (Pin + `context_with_data`), GCP via google libs (LLM pattern) |
| database | `psycopg/patch.py` | `mysql/patch.py` | SQL clients, `db.*` span tags, DBM support, uses Pin + dbapi helpers |
| faas | `aws_lambda/patch.py` | `azure_functions/patch.py` | Serverless function wrappers, uses `ddtrace.internal.wrapping` |
| generative-ai | `anthropic/patch.py` | `litellm/patch.py` | LLM/AI integrations; use `llmobs-integrations` for LLMObs lifecycle, extraction, streaming, and tests |
| graphql | `graphql/patch.py` | -- | GraphQL resolvers and operations, shared tracing gate + `tracer.trace` |
| http-client | `httpx/patch.py` | `requests/connection.py` | Outbound HTTP, `http.*` span tags. httpx and requests use `context_with_event` |
| http-server | `flask/patch.py` | `django/patch.py` | Web frameworks, request/response spans, Pin + `context_with_data` |
| logging | `logging/patch.py` | `loguru/patch.py` | Log correlation injection (trace ID, span ID) -- no spans created |
| messaging | `kafka/patch.py` | `kombu/patch.py` | Message brokers, DSM support, Pin + `tracer.trace` |
| object-store | `botocore/patch.py` (S3) | -- | S3 via botocore service-specific handlers |
| orchestration | `celery/patch.py` | -- | Task orchestration, distributed tracing, Pin + `tracer.trace` via signals |
| rpc | `grpc/patch.py` | -- | RPC frameworks, client + server spans, Pin + `tracer.trace` |

## LLM / Generative AI Detail

This APM reference lists LLM/AI integrations only to help choose comparable
contrib patch modules. For LLMObs-specific architecture, provider extraction,
streaming, and test transport guidance, use the `llmobs-integrations` skill.

### Gateway callback attribution

LiteLLM's optional `gateway.py` is not SDK auto-instrumentation. It binds verified
proxy authentication to terminal callbacks using opaque process-local tokens and
emits content-free APM usage spans. Do not turn these into LLMObs request spans
with prompt/response extraction, trust client metadata as authenticated identity, or infer a
billing account from the model provider. The public opt-in entry point is
`ddtrace.contrib.litellm.gateway_attribution`; its proxy tests live under
`tests/contrib/litellm/gateway/`.

The callback also collects LiteLLM's normalized `end_user_id` by default as
`ai.end_user.id` with unverified trust. Only when authenticated `user_id` is absent
may this fill `usr.id`, with source `litellm_end_user`; keep the
`authenticated_user_unknown` issue. Never re-read raw headers/body to recover an ID
LiteLLM omitted, copy JSON-shaped identity payloads, or use end-user claims for
authenticated enrichment or billing scope. `capture_end_user=false` opts out;
invalid configuration disables end-user capture too.

`_gateway_metadata.py` selects route and pricing fields for privacy, distinguishing
ingress from provider-transformed outgoing settings. Keep valid values verbatim,
including unfamiliar provider names, traffic types, tiers, cache types, and TTLs;
never use enum-value allowlists or billing-value mappings. Type, size, and secret
checks still apply. Never dump logging kwargs or whole header/provider-specific
dictionaries. Do not infer billing provider/account/mode/geography or add manual
billing overrides. Consumers interpret these observations downstream.
Only outgoing provider headers may supply non-secret OpenAI organization/project
IDs; do not read ingress headers or stringify endpoint objects. Keep provider
model_id/resource_id intact without parsing ARNs; they are not the router's hidden
model_id. OCI scope uses explicit route IDs, not credential-file inspection.
Missing cache-control TTL stays unspecified, not an assumed default. Preserve
cache-counter presence; absent cache detail cannot establish an uncached-input partition.
Explicit modality counters stay diagnostic when their overlap
with caching is unknown. Cache-control TTLs are not per-TTL token quantities.
The constructor keeps both legacy and current LiteLLM message-logging flags off;
the ordinary SDK matrix also imports/tests the callback against older LiteLLM.
