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
| http-server | `flask/patch.py` | `django/patch.py` | Flask typed request with `SpanLifecycleSubscriber` and deferred response completion; Django retains `context_with_data` |
| logging | `logging/patch.py` | `loguru/patch.py` | Log correlation injection (trace ID, span ID) -- no spans created |
| messaging | `kafka/patch.py`, `aiokafka/patch.py`, `kombu/patch.py` | `rq/patch.py` | Kafka, aiokafka, and Kombu use shared messaging events; RQ still uses Pin |
| object-store | `botocore/patch.py` (S3) | -- | S3 via botocore service-specific handlers |
| orchestration | `celery/patch.py` | -- | Task orchestration, distributed tracing, Pin + `tracer.trace` via signals |
| rpc | `grpc/patch.py` | -- | RPC frameworks, client + server spans, Pin + `tracer.trace` |

For GraphQL, keep execute as the execution hook on older versions. GraphQL 3.3's
execute_sync bypasses execute and needs its own hook; do not add that hook on
older versions, where it would duplicate execution spans. Middleware argument
positions differ between execute and execute_sync and changed in 3.2.10 and 3.3.

## LLM / Generative AI Detail

This APM reference lists LLM/AI integrations only to help choose comparable
contrib patch modules. For LLMObs-specific architecture, provider extraction,
streaming, and test transport guidance, use the `llmobs-integrations` skill.

## ElevenLabs Agents

The ElevenLabs patch module wraps conversation worker lifecycle and uses
conversation-local WebSocket proxies. It replaces only the SDK's imported
asynchronous module binding; the global websockets module remains unchanged.
The SDK's audio interfaces and application callbacks are preserved. Startup failure
or cancellation finalizes only newly created state; retry captures its own caller
context, while redundant startup cannot close an ongoing session. See the LLMObs
implementation guide for per-turn span layout and audio timing eligibility.
If external shutdown fails or is cancelled before the SDK sets its stop signal,
restore the previous closing state so the running worker remains observable.
Keep closing set after the stop signal or worker finalization, even if an end
callback fails.

Optional raw vad_score events are observed by the same connection-local proxies
before SDK filtering. They do not require new callbacks, patch points, or event
subscriptions. See the LLMObs guide for the bounded clip-relative activity schema.


ElevenLabs protocol/audio state is product-owned in llmobs/_integrations/elevenlabs_agents.py.
Contrib wrappers depend only on a neutral state Protocol and request state through
core event elevenlabs.conversation.start. LLMObs registers/removes the factory with
its service lifecycle. Client-tool handler bodies scope both APM and LLMObs contexts,
restoring caller contexts on success, failure, and cancellation. Synthetic replay
fixtures are stored as deterministic gzip JSON to preserve exact event/audio data
within the added-file size limit.

ElevenLabs captures explicit routing (including no routing) at conversation startup
and scopes it around span creation and client-tool handlers. Raw SDK threads must
not pick up another conversation's or worker's destination. Completion state is
separate from retained turns: later failures affect only unfinished responses,
and interruptions affect unfinished generation or audio still playing. A newer
turn also retires older generation when optional completion events are absent.
Pre-metadata audio overflow discards the whole buffered prefix and carries the
omission flag into the next input buffer; it must not export a truncated WAV.

ElevenLabs includes the standard PatchTestCase lifecycle matrix for every wrapped
entry point and checks that the global WebSocket module is left untouched.
Public-SDK smoke tests install fake transport before ordinary patching and use
public session start/end/wait methods. Focused state-machine tests remain useful
for malformed events, late delivery, and buffer limits. Accepted streamed text
observations advance the response end-time fallback; ignored late parts do not.
The provider conversation ID replaces the bootstrap UUID when metadata arrives.

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
