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

Bottle supplies request and route values to the shared HTTP controller. The HTTP
tracing subscriber selects Bottle conventions and builds the historical METHOD rule
resource; route data retains the script_name prefix for http.route.

TracePlugin delegates framework hook installation to BottleHooks in
bottle/lifecycle.py. HttpLifecycle in contrib/internal/http_server.py owns identity,
observations, block checks and preservation, and completion. WsgiLifecycleDriver in
contrib/internal/http_wsgi.py owns activation, response delivery, iteration and
close. Other WSGI integrations reuse both shared layers and supply request extraction,
framework response conversion, routing/handler hooks, and handled-error capture.
The shared driver has no Bottle dependency. Hook lookup is scoped to its driver so
nested applications do not use another application's controller.

Route-local plugins retain _trace_route_callback and use create_http_server_context
because they cannot observe the WSGI boundary. The normal WSGI path uses the same
factory through HttpLifecycle. Both preserve tracing and propagation settings.

IDM owns HTTP semantics and the completion decision. The shared HTTP tracing
subscriber applies those semantics; core supplies context and span lifecycle
mechanisms. The lifecycle finalizes the resource before ending its context, so
completed-span consumers see the endpoint. Each WSGI call and body operation runs
in the request's copied context, preventing suspended streams from becoming the
active parent of unrelated requests. Exhaustion and close share an idempotent
completion path. A WSGI server must consume or close the response iterable.


Bottle also publishes `HttpRequestDataEvent` from `contrib/_events/http.py` at
request entry, route resolution, before the wrapped handler, response preparation,
response commitment, and completion. Its phase-specific events expose HTTP values directly,
availability, lifecycle flags, and an opaque request token independently of
`HttpServerRequestEvent` tracing settings. The shared HTTP server subscriber
correlates observations by request_id and selects conventions by framework. Consumers treat event envelopes as read-only
and may retain their frozen request, response, and error values. `None` means unavailable; empty strings
and tuples mean known empty. Bodies are not read for this contract.

A registered data consumer keeps the application lifecycle active when tracing is
disabled, without creating a request span. A block-check subscriber also activates
this lifecycle. `HttpRequestBlockCheckEvent` lets a synchronous listener raise
`HttpRequestBlock(status_code=403, headers=(), body=b"")`. The exception derives from
BaseException so the hub propagates it while isolating ordinary listener failures.
The first block skips remaining check listeners. Bottle validates the response and
uses normal response and completion handling; invalid blocks are ignored. A valid
block suppresses subsequent checks. There are no checks after commitment and no
stream termination API. Never implement security policy in the integration or import
AppSec here. See `docs/contributing-integrations.rst` for timing and ownership rules.


Handler checks wrap the resolved route's callback factory outside its complete
plugin chain. Trace-plugin skip settings must not disable HTTP observations or
blocking; wrapping the factory preserves this boundary across route cache resets.
An accepted block is kept as immutable HTTP data until the first start_response
attempt so application hooks cannot replace its status, headers, or body. Clear
that pending response before delivery so a framework error retry cannot reuse it.

The opt-in Starlette proof in starlette/patch.py reuses HttpLifecycle through
http_asgi.py. instrument_http is a context manager that installs reversible route
hooks; it is not installed by patch(). The ASGI driver needs only get_config and
provides request extraction, handler wrapping, and block enforcement. An optional
request_data callback preserves framework-specific normalization. There is no
per-integration lifecycle module or required framework adapter class. complete() publishes final HTTP
observations independently of finish(), which exits context in its owning task.
ASGI contexts opt into finish_on_response so successful tracing ends before
background work even when send executes in a child task. Error responses defer
completion until application unwind. The shared subscriber owns mounted-resource
conventions; producers report both full routes and local patterns. See
examples/http-events/README.md for prototype limits before reusing this adapter.
