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

## LLM / Generative AI Detail

This APM reference lists LLM/AI integrations only to help choose comparable
contrib patch modules. For LLMObs-specific architecture, provider extraction,
streaming, and test transport guidance, use the `llmobs-integrations` skill.

## Bottle HTTP event boundary

Bottle builds its own `METHOD rule` span resource and passes it as
`WebFrameworkRequestEvent.resource`. `request_route` stays separate and carries the
`script_name` prefix, so mounted applications report the full `http.route` without
changing their historical span resource.

An application-installed plugin wraps Bottle's WSGI boundary and owns a request
lifecycle through response exhaustion, failure, or explicit close. Its router
wrapper supplies the resource as soon as routing succeeds, before handler
invocation. The WSGI start_response callback supplies the final status and headers; HTTPError conversion preserves
exceptions that Bottle handles internally. Unmatched requests use METHOD STATUS
resources. Route-local plugins retain their callback-scoped behavior.

IDM owns HTTP semantics and the completion decision. The shared HTTP tracing
subscriber applies those semantics; core supplies context and span lifecycle
mechanisms. The lifecycle finalizes the resource before ending its context, so
completed-span consumers see the endpoint. Each WSGI call and body operation runs
in the request's copied context, preventing suspended streams from becoming the
active parent of unrelated requests. Exhaustion and close share an idempotent
completion path. A WSGI server must consume or close the response iterable.


Bottle also publishes `HttpRequestDataEvent` from `contrib/_events/http.py` at
request entry, route resolution, before the wrapped handler, response preparation,
response commitment, and completion. Its immutable snapshot carries HTTP values,
availability, lifecycle flags, and an opaque request token independently of
`WebFrameworkRequestEvent` tracing settings. Consumers retain snapshots rather than
framework objects or mutable tracing events. `None` means unavailable; empty strings
and tuples mean known empty. Bodies are not read for this contract.

A registered data consumer keeps the application lifecycle active when tracing is
disabled, without creating a request span. An inspection subscriber also activates
this lifecycle. `HttpRequestInspectionEvent` exposes synchronous `submit(decision)`;
Bottle validates and enforces replacement responses before commitment and stops body
production for termination at commitment. Decision callbacks close when dispatch ends.
Never implement security policy in the integration or import AppSec here. The adapter
advertises only actions it can enforce at that phase. The API history and exact failure,
late-decision, and transport rules live in `docs/http-contract-api-changes.rst`. See the HTTP request-data contract in
`docs/contributing-integrations.rst` for phase timing, ownership, and lifetime rules.

## ASGI HTTP observation scaffold

`contrib/internal/asgi/lifecycle.py` provides an explicitly installed
`HttpLifecycleMiddleware` that emits immutable HTTP data snapshots without creating
spans or dispatching inspection events. It forwards ASGI messages unchanged and
marks response commitment only after the downstream start send succeeds.
Completion follows application return, including background work and cancellation.
The optional Starlette route hook runs before the tracing-context guard, accumulates
mount templates, and emits leaf routing phases without depending on spans.
All enforcement capabilities are empty. LLMObs integration patterns are unaffected.
