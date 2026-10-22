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

## Bottle plugin boundary

Bottle's TracePlugin.apply wraps only the route callback and emits
WebFrameworkRequestEvent. WebFrameworkRequestSubscriber owns span creation,
metadata handling, and completion for that event. Bottle manages plugin ordering, skip settings,
installation, removal, and callback-cache resets.

Read request metadata from Bottle's request at callback entry. The span ends when
the callback returns or raises, including when it returns an unconsumed iterable.
Later response hooks, streaming failures, and transport delivery are outside this
boundary. Do not wrap router.match, _cast, or _make_callback to extend it.


Bottle also dispatches WebFrameworkCallbackStartedEvent and
WebFrameworkCallbackCompletedEvent around the callback, inside the optional tracing
context. Their callback_id correlates an invocation; metadata is copied and no
body is consumed. Consumers own product setup and cleanup. Completion precedes
streaming and later response hooks. Neither event is a blocking channel.

The plugin bypasses instrumentation only when tracing is disabled and neither
callback event has listeners. Notifications still follow plugin skip settings;
this is not coverage of every HTTP request.

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
