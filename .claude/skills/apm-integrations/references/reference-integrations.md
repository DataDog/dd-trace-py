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

## Shared synchronous web request runner

contrib.internal.web_framework.run_web_framework_request owns request preparation,
context entry/exit, callback invocation, and safe metadata completion. It imports
no framework and knows nothing about HTTPResponse, body cleanup, or blocking.
The prepare and complete hooks extract metadata; subscribers own span behavior.
Ordinary metadata errors must not replace the application's outcome. Preparation
failure bypasses context creation and the execution adapter, calling the application
once. Disabled tracing must not skip metadata hooks or context events.

An optional execute(event, invoke) adapter runs inside the context. invoke calls
the application with its original arguments, completes metadata, then returns or
re-raises. Adapters may inspect that metadata and replace the outcome. If they skip
or replace the callback, they own final metadata completion using
complete_request_safely. Never invoke the application twice or manage spans in an
adapter. Completion remains at callback return/raise; async and deferred response
lifetimes need a different runner. Returned iterables are not consumed or wrapped.

## Bottle plugin boundary

Bottle's TracePlugin.apply delegates through a small _run_request wiring helper to
the shared runner. _execute_request is its framework-specific execution adapter:
it dispatches typed request/response notifications, catches BlockingException
(including exception groups), closes discarded bodies, builds HTTPResponse, and
completes replacement metadata. AppSec subscribers own WAF evaluation, the
block_request callable, response selection, and security-context cleanup.

Bottle uses context_name_override="bottle.request" with WebFrameworkRequestEvent;
the shared tracing subscriber listens to both web.request and bottle.request.
AppSec cleanup uses a ContextSubscriber for bottle.request. Notifications use
BottleRequestStartEvent, BottleResponseEvent, and BottleBlockResponseEvent through
core.dispatch_event. The block event carries the selected response back to the
adapter; do not use string-keyed context items for it. Request WAF evaluation stays
inside the blocking handler because raising during context entry skips context exit.

Response inspection uses headers from a returned or raised HTTPResponse when
present. Replaced iterables are closed without being consumed. Later response hooks,
streaming failures, transport delivery, and request/response body inspection are
outside this boundary. Do not wrap router.match, _cast, or _make_callback to extend
it. Bottle manages plugin ordering, skip settings, installation, removal, and
callback-cache resets.

## Specialized duplex SDKs

For independently consumed input/output streams, see
`aws_sdk_bedrock_runtime/patch.py` and `_stream.py`. Wrap only the target
SDK operation and its returned connection, not global Smithy classes.
These audio-turn spans use the specialized direct-span LLMObs lifecycle;
see the LLMObs implementation guide for parenting and finalization rules.

The Bedrock wrapper dispatches a typed observer handoff event through core. The
LLMObs service registers its handler on enable and removes it on disable; contrib
does not import the product. Stream proxies depend only on the observer protocol.

Input context-manager exits must record body and cleanup errors without changing
exception propagation or suppression. A successful input exit only half-closes;
it must not finalize the observer before the output stream drains.
All three proxies must finalize entry failures too: Python does not call
`__aexit__` when `__aenter__` raises, including cancellation.

Delegate output iteration to the wrapped receiver's `__anext__` so the SDK owns
EOF cleanup. Finalize telemetry after that operation returns or raises, including
cleanup failures. Direct `receive()` retains its separate SDK half lifecycle.
