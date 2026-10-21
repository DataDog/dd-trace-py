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
| messaging | `kafka/patch.py`, `aiokafka/patch.py`, `kombu/patch.py` | `rq/patch.py` | Kafka, aiokafka, and Kombu use shared messaging events; RQ still uses Pin |
| object-store | `botocore/patch.py` (S3) | -- | S3 via botocore service-specific handlers |
| orchestration | `celery/patch.py` | -- | Task orchestration, distributed tracing, Pin + `tracer.trace` via signals |
| rpc | `grpc/patch.py` | -- | RPC frameworks, client + server spans, Pin + `tracer.trace` |

## HTTP Request Lifecycle Notifications

FastAPI is the first IAST integration migrated to `WebRequestReady` and
`WebRequestCompleting`. ASGI middleware emits these events from
`ddtrace.contrib._events.web_request`, carrying the existing request
`ExecutionContext` and `SpanData`. Emit ready after span binding and completing
before span finish on every exit path. Completion can run in a response task or
callback; a lexical scope exit alone is not completion for a deferred response
iterator. Products own their handlers, cleanup and nested-request bookkeeping.

ASGI guards dispatches with subscriber checks and allocates a completion callback
only when Completing has subscribers. An outer try/finally covers setup failures,
application execution and teardown. Completion is dispatched once, including
when a subscriber interrupts dispatch with cancellation. Callbacks can use
`core.activate_context(ctx)` to temporarily activate an existing context without
re-emitting its scope events. Product-specific state handling belongs in the
product's listeners.

Keep product lifecycle behavior in shared IAST tests and integration-specific
IAST cases under `tests/appsec/integrations/`, outside the contrib suites. During
migration, `test_processor_request_events.py` covers only coexistence routing and
span exclusions; delete that file with `AppSecIastSpanProcessor`.
For integrations already covered by IAST tests, extend those tests to check the
event boundaries instead of adding another lifecycle matrix.

## LLM / Generative AI Detail

This APM reference lists LLM/AI integrations only to help choose comparable
contrib patch modules. For LLMObs-specific architecture, provider extraction,
streaming, and test transport guidance, use the `llmobs-integrations` skill.
