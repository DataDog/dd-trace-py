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


## Bottle HTTP event boundary

Bottle uses WebFrameworkRequestEvent.resource_route to opt in to shared METHOD route
resource naming. The event derives the resource before span creation and preserves
explicit resource overrides; request_route remains separate so mounted applications
can report the full HTTP route without changing their historical span resource.
Bottle extracts its local response via the existing set_response API, then resolves
framework-specific returned/raised HTTPResponse status overrides. The shared tracing
subscriber owns HTTP tagging and span completion; Bottle retains the callback boundary.


## Starlette final-route boundary

Starlette (including the handler reused by FastAPI) dispatches
HttpServerRouteResolvedEvent from contrib/_events/http.py for the final Route,
after resource/route resolution and before set_http_meta and asgi.start_request.
Intermediate Mount handlers do not emit it. This dispatch-only event carries the
method, resolved route, and live path-parameter mapping; it creates no context or
span. Synchronous listeners may replace parameter values in place, but must
preserve their application meaning. IAST registers its listener on product
activation and owns endpoint attribution and parameter tainting.

This is an incremental boundary: resource naming, HTTP metadata, the existing
WAF trigger/block enforcement, and ASGI response completion still use their
existing paths. It is not yet a generic blocking or span-completion contract.
