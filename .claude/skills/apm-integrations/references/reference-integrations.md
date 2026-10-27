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

An optional execute(event, invoke, kwargs, complete) adapter runs inside the context.
invoke calls the application with its original arguments, completes metadata, then
returns or re-raises. Adapters may inspect that metadata and replace the outcome. If
they skip or replace the callback, they own final metadata completion using
complete_request_safely. The runner passes kwargs and complete per request, so adapters
stay stateless and are built once at import. Never invoke the application twice or
manage spans in an adapter. Completion remains at callback return/raise; async and
deferred response lifetimes need a different runner. Returned iterables are not
consumed or wrapped.

blocking_execute_adapter builds the shared blocking adapter: it dispatches the
inspection events, catches BlockingException (including exception groups), closes the
discarded outcome, resolves the blocked response, and completes replacement metadata.
Integrations supply only four framework callbacks -- request_details(kwargs) returning
RequestInspectionDetails, is_response_object(outcome), extract_body(outcome), and
build_response(status, headers, body) -- and no blocking logic. Without a block-response
subscriber it falls back to a default JSON 403. Reuse this factory for new synchronous
frameworks; write a custom execute only for a different inspection sequence.

## Bottle plugin boundary

Bottle's TracePlugin.apply delegates through a small _run_request wiring helper to
the shared runner. _execute_request is a module-scope blocking_execute_adapter built
from four Bottle callbacks: _request_details reads peer IP, cookies, and path params
from Bottle's request proxy; _is_http_response recognizes HTTPResponse; _response_body
reads its body; _build_blocked_response constructs the replacement and applies it over
the pending response so completion hooks skip application headers and cookies. Bottle
contributes no blocking control flow. AppSec subscribers own WAF evaluation, the
block_request callable, response selection, and security-context cleanup.

Bottle uses context_name_override="bottle.request" with WebFrameworkRequestEvent;
the shared tracing subscriber listens to both web.request and bottle.request.
AppSec inspection and block-response subscribers live in appsec/_contrib/web_framework;
they consume explicit peer IP, cookies, and path parameters rather than library request
objects. The shared cleanup subscriber listens to both web.request and bottle.request.
Reuse its event-name list for identical cleanup; specialize only for different
behavior. Notifications use WebFrameworkRequestInspectEvent,
WebFrameworkResponseInspectEvent, and WebFrameworkBlockResponseEvent through
core.dispatch_event. The block event carries the selected response back to the
shared adapter; do not use string-keyed context items for it. Request WAF evaluation
stays inside the blocking handler because raising during context entry skips context exit.

Bottle marks response_from_exception when HTTPResponse is raised so the tracing
subscriber can apply OTel status-based error semantics without suppressing the
application exception. Replacement responses clear that flag.

Response inspection uses headers from a returned or raised HTTPResponse when
present. Replaced iterables are closed without being consumed. Later response hooks,
streaming failures, transport delivery, and request/response body inspection are
outside this boundary. Do not wrap router.match, _cast, or _make_callback to extend
it. Bottle manages plugin ordering, skip settings, installation, removal, and
callback-cache resets.


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
