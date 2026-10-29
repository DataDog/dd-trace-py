# LLMObs Integration Guide

Follow the **apm-integrations** skill's [Implementation Guide](../../apm-integrations/references/implementation-guide.md) for patch registration, configuration, and tests. This guide covers LLM-specific behavior. Paths are repository-relative; integration filenames in the table below are under `ddtrace/llmobs/_integrations/`.

## Integration class

Before writing anything, read the existing implementation that most closely matches your library's pattern:

| Pattern | Reference Integration | When to use |
|---------|----------------------|-------------|
| Standard LLM (chat completion) | `anthropic.py` | Library generates text responses from messages |
| Agent runs + tool child spans | `claude_agent_sdk.py` | Library manages an agentic loop with tool calls |
| Embeddings | `openai.py` | Library generates vector embeddings |

Create `ddtrace/llmobs/_integrations/{name}.py` with a `BaseLLMIntegration` subclass and
the provider's `_integration_name`. Supply the two provider-specific behaviors:

- `_set_base_span_tags(span, **kwargs)` sets the integration's base APM attributes.
- `_llmobs_set_tags(span, args, kwargs, response=None, operation="")` extracts LLMObs data
  and annotates it through `_annotate_llmobs_span_data()`.

Use the actual base/reference signatures and implement or inherit the behavior needed by the
integration. For standard request integrations, construct it lazily in the LLMObs subscriber
with `integration_config=config.<name>`; see `ddtrace/llmobs/_contrib/_subscribers.py`.
Keep contrib free of LLMObs imports. When extending an integration that stores its instance on
`module._datadog_integration`, preserve that ownership and cleanup unless migrating its lifecycle.

Keep concrete `Span` imports out of `ddtrace/llmobs/_integrations/` to avoid a
`product:llmobs -> product:tracing` dependency. Choose the parameter type by the required interface:

- Use `_LLMObsSpan` from `ddtrace.llmobs._span` for `_llmobs_set_tags()` and helpers that call
  `_annotate_llmobs_span_data()` or need context-item methods such as `_set_ctx_item`.
  The runtime `Span` satisfies this structural protocol. Extend the shared protocol when another
  member is needed; do not create per-module copies or bypass it with `cast(Any, span)`.
- Use `SpanData` from `ddtrace.internal.native._native` only for operations its interface supports,
  such as `_set_attribute`, `_get_struct_tag`, and `_set_struct_tag`. A bare `SpanData` lacks
  `_set_ctx_item` and does not satisfy `_LLMObsSpan`.

Read the current method signatures in `BaseLLMIntegration` and the closest integration.
Implement only the extraction helpers needed by the provider. Register exported classes in
`_INTEGRATION_MODULES` in `ddtrace/llmobs/_integrations/__init__.py` to preserve lazy loading.

## Patch lifecycle (`LlmRequestEvent`)

Standard LLM integrations should use `LlmRequestEvent` from `ddtrace/contrib/_events/llm.py` with `core.context_with_event()`. Read `ddtrace/contrib/internal/anthropic/patch.py` for the event-based pattern. The patch layer stores the response on `event.response` and calls `ctx.dispatch_ended_event()`. `LlmTracingSubscriber` in `ddtrace/_trace/subscribers/llm.py` handles span creation, errors, and finishing, dispatching `SpanCycleEvents` (`ddtrace/contrib/_events/span_cycle.py`) so product subscribers can add tags and extract LLMObs data.

Key points:
- Construct `LlmRequestEvent(..., submit_to_llmobs=True, request_kwargs=kwargs, ...)` and leave `llmobs_integration` unset for subscriber-based integrations
- Set contrib-owned APM tags through the event's `tags`; pass `None` as the stream handler's integration when contrib has none
- The event/subscriber path owns span creation and finishing; patch wrappers should not call `tracer.trace()`, `integration.trace()`, or create spans directly for standard request spans
- Use `with core.context_with_event(event, dispatch_end_event=False) as ctx:` when streaming or when the wrapper needs to dispatch the ended event manually
- For non-streaming success, set `event.response = resp` and call `ctx.dispatch_ended_event()`
- For errors, call `ctx.dispatch_ended_event(*sys.exc_info())` and re-raise; do not call `span.set_exc_info()` or `span.finish()` directly in the patch wrapper
- LLMObs tag setting is handled by product subscribers, not directly in the patch wrapper
- The async variant is identical but uses `async def` / `await`

Preserve direct calls to `integration.trace()` and `integration.llmobs_set_tags()` where the integration owns that lifecycle (for example Google GenAI). Use direct spans when the behavior requires them, such as OpenAI MCP tool spans or agent/tool child spans; keep their creation, extraction, and finishing under one owner.

Existing events carrying `llmobs_integration` are handled directly by `LlmTracingSubscriber`.
Preserve that contract when extending those integrations; use product subscribers for new
standard request integrations so contrib does not import LLMObs.

## LLMObs subscribers and registration

Follow `ddtrace/llmobs/_contrib/anthropic/` for integration registration. The shared subscribers
in `ddtrace/llmobs/_contrib/_subscribers.py` receive an `ExecutionContext` and select the
registered integration using `ctx.event.component`:

- `SpanCycleEvents.SPAN_STARTING`: before span creation, set `event.span_type = SpanTypes.LLM` when
  `event.submit_to_llmobs` and the integration's LLMObs enablement require it.
- `SpanCycleEvents.SPAN_STARTED`: set base tags, annotate the integration, and stamp agent kind/name
  before children start. Preserve APM shadow tags and proxy handling from the reference.
- `SpanCycleEvents.SPAN_FINISHING`: extract data with `integration.llmobs_set_tags()` before finishing.

Provide `listen()` / `unlisten()` in `ddtrace/llmobs/_contrib/<name>/__init__.py`.
Call the shared `register(component, integration_cls)` from `listen()` and
`unregister(component)` from `unlisten()`; do not create per-integration subscriber classes.
Shared listeners remain registered; unregistering removes only that component from the registry
and cache. Each started request retains its integration on its execution context, so in-flight
requests and deferred streams still finalize after unpatch or repatch. Build the integration
lazily after its config is registered. Subscribers also set APM shadow tags, so registration
must work with LLMObs disabled.

Declare the subscriber module in `pyproject.toml`, substituting the integration's name:

```toml
[project.entry-points.'ddtrace.integration_hooks']
<name> = "ddtrace.llmobs._contrib.<name>"
```

The contrib's `patch()` calls `integration_hooks.on_patch("<name>")` after wrapping;
`unpatch()` calls `integration_hooks.on_unpatch("<name>")` before unwrapping.
The helper in `ddtrace/internal/integration_hooks.py` loads the registered module only when
that integration is patched. This supports `ddtrace-run`, `import ddtrace.auto`, and direct
patch calls; do not add registration to `ddtrace.patch()` or `ddtrace/_monkey.py`.
Rebuild the selected test environment with `scripts/run-tests -r --venv <hash>` after adding
an entry point, since discovery uses installed package metadata.

## Streaming

Subclass `StreamHandler`/`AsyncStreamHandler` from `ddtrace/internal/utils/streaming.py`, and import `make_traced_stream` from there too. `ddtrace/contrib/internal/stream_handler.py` only re-exports these for backwards compatibility; do not add new imports from it:

- `initialize_chunk_storage()` — set up accumulators for content, usage, role
- `process_chunk(chunk)` — accumulate text, tool blocks, usage from each chunk
- `finalize_stream(exception)` — build the final response and complete the deferred span lifecycle. For `LlmRequestEvent` integrations, set `ctx.event.response` and call `ctx.dispatch_ended_event(...)`; direct-trace integrations may need to call `llmobs_set_tags()` and `span.finish()` themselves. Call this via `close_stream()` (including eager finish from `process_chunk`, as Claude Agent SDK does). `close_stream()` invokes it at most once. Retain a local idempotency guard when existing callers can also invoke `finalize_stream()` directly.

`TracedStream` / `TracedAsyncStream` invoke `close_stream()` from `__iter__`/`__aiter__` (`finally`), from `StopIteration`/`StopAsyncIteration` on `__next__`/`__anext__`, from `__exit__`/`__aexit__` after the wrapped stream exits, and from `__del__` as a last resort. `__exit__`/`__aexit__` catch `BaseException` so `asyncio.CancelledError` from wrapped cleanup still finalizes. That path matters: a caller that uses `with stream:` and does not exhaust the iterator still finishes the span.

When `__enter__` wraps a stream manager, the parent retains the child wrapper for the `with` body so `__del__` cannot finalize the shared handler early. `on_stream_created` runs before that retain; if it raises, the handler is finalized immediately because Python will not call `__exit__`.

Wire into patch with `make_traced_stream(response, handler)`.

For the agent pattern (tool child spans within streams), see `ddtrace/llmobs/_integrations/claude_agent_sdk.py`.

## Message Extraction

Different libraries structure messages differently — implement only the helpers you need. Read `ddtrace/llmobs/_integrations/anthropic.py` for full code examples.

**Check `ddtrace/llmobs/_integrations/utils.py` first.** Providers reachable through more than one API share a payload format, and the extraction for it belongs there rather than in each integration. Anthropic content blocks, for example, are parsed by `get_messages_from_anthropic_content()`, `anthropic_tool_call_from_block()`, `anthropic_tool_result_from_block()`, and `get_tool_definitions_from_anthropic_tools()`, which the Anthropic and Amazon Bedrock integrations both call. Converse-format payloads have their own equivalents. Reuse or extend those helpers instead of copying the logic; duplicated parsers drift as providers add content block types.

- `_extract_input_messages(kwargs)` — handle system message, multi-part content blocks, tool results
- `_extract_output_messages(response)` — handle text blocks, tool use blocks, thinking blocks
- `_extract_usage(response)` — map library-specific usage fields (tokens, server-side tool calls) to metric keys
- `_extract_tools(kwargs)` — convert `tools` kwarg to `ToolDefinition` list
- `_extract_audio_parts(...)` — for multimodal audio providers, populate `Message.audio_parts` with `AudioPart` entries containing `mime_type` plus either inline base64 `content` or an `attachment_key`
- `_extract_<lib>_image_source(...)` — same, for `Message.image_parts`. See `anthropic.py`

Keep provider-specific message, tool, metadata, and usage normalization in the integration subclass. Put extraction shared by multiple integrations in `ddtrace/llmobs/_integrations/utils.py`. The patch wrapper supplies the request and response to the integration.

### Inline Media Size Guards

Oversized inline media makes `_writer._truncate_span_event` blank the span's entire input **and** output, not just the offending field. So route media through `format_audio_part_with_guard()` / `format_image_part_with_guard()`, which return `None` and let the caller keep a text marker.

- Pass raw data, not a pre-encoded string, so an oversized payload is rejected before paying to encode it.
- Do non-size rejection (bad MIME, non-ASCII "base64", a `Path`) in the *extractor*. If the guard returns `None` for a non-size reason, the caller's "too large" marker starts lying.
- Each guard bounds one part; it does not enforce a combined payload budget. Several individually valid parts can still exceed the event limit.

### Metadata Hygiene

Do not dump raw `kwargs` into LLMObs metadata. Prefer shared helpers such as `get_metadata_from_kwargs()` and provider-specific helpers in `ddtrace/llmobs/_integrations/utils.py` when they apply. They filter provider sentinel defaults such as OpenAI `Omit`/`NotGiven`, serialize SDK objects through `load_data_value()`, and keep metadata limited to supported fields. `_annotate_llmobs_span_data()` coerces metadata keys to strings, but integrations should still avoid sending noisy or high-cardinality raw objects.

### Token Field Mapping

| Library | Input | Output | Cache Fields |
|---------|-------|--------|-------------|
| Anthropic | `input_tokens` | `output_tokens` | `cache_creation_input_tokens`, `cache_read_input_tokens` |
| OpenAI chat | `prompt_tokens` | `completion_tokens` | `prompt_tokens_details.cached_tokens` (already included in input total), `prompt_tokens_details.cache_write_tokens` |
| OpenAI responses | `input_tokens` | `output_tokens` | `input_tokens_details.cached_tokens` (already included in input total), `input_tokens_details.cache_write_tokens`, and its TTL split `input_tokens_details.cache_write_tokens_details.ttl_30m_tokens` / `ttl_12h_tokens` |
| Google GenAI | `prompt_token_count` | `candidates_token_count` | `cached_content_token_count` |

Normalize `INPUT_TOKENS_METRIC_KEY` to the total input tokens sent to the model, including cached and non-cached tokens. Providers report this differently: Anthropic reports non-cached `input_tokens` separately from cache read/write input tokens, so add `input_tokens + cache_creation_input_tokens + cache_read_input_tokens`; OpenAI reports prompt/input tokens as the combined total and exposes cached tokens separately in details.

When a provider splits cache writes by TTL because each TTL is billed at a different rate, report the split next to `CACHE_WRITE_INPUT_TOKENS_METRIC_KEY`: Anthropic `cache_creation.ephemeral_5m_input_tokens` / `ephemeral_1h_input_tokens` map to `ephemeral_5m_input_tokens` / `ephemeral_1h_input_tokens`, and OpenAI `ttl_30m_tokens` / `ttl_12h_tokens` map to `ephemeral_30m_input_tokens` / `ephemeral_12h_input_tokens`.

For OpenAI Realtime, read the singular `input_token_details` and
`output_token_details` fields. Preserve `audio_tokens` as `input_audio_tokens` /
`output_audio_tokens`, `cached_tokens` as `cache_read_input_tokens`, and
`cached_tokens_details.audio_tokens` as `cache_audio_read_tokens`. These are subsets
of the inclusive totals. Preserve explicit zero counts and omit missing breakdowns;
never infer zero cached audio from a missing cached-token detail object. Preserve
`text_tokens` as `input_text_tokens` / `output_text_tokens`, input `image_tokens`
as `input_image_tokens`, and cached text/image counts as `cache_text_read_tokens` /
`cache_image_read_tokens`. Only emit nonnegative integer counts (not booleans);
never infer text counts by subtracting audio from totals.

### Server-Side Tool Usage

Some providers run tools themselves and bill per call on top of token costs. Record those billable calls as LLM-span metrics next to the token counts:

| Metric key | Meaning |
|------------|---------|
| `WEB_SEARCH_COUNT_METRIC_KEY` (`web_search_count`) | Provider-executed web searches |
| `STORAGE_SEARCH_COUNT_METRIC_KEY` (`storage_search_count`) | Provider-executed file / vector-store searches |

- Count billable calls, not queries or results. One call can carry several queries.
- Prefer a count the provider reports, and only count output items when nothing reports one:
  - Anthropic: `usage.server_tool_use.web_search_requests`.
  - OpenAI Responses web search: the untyped top-level `tool_usage.web_search.num_requests` (documented as the billing count by Azure OpenAI). When it is absent, count completed `web_search_call` items whose `action.type` is `search`; `open_page` / `find_in_page` steps are not billed.
  - OpenAI file search: no counter exists, so count completed `file_search_call` items.
  - See `get_openai_server_tool_usage_metrics()` in `_integrations/utils.py`.
- Omit the metric when the count is absent or 0; never write 0.
- Skip server tools with no per-call price (for example Anthropic `web_fetch_requests`).
- For streams, read the count from the final usage event (Anthropic `message_delta`, OpenAI `response.completed`), not from content block events.

## Agent Integrations: Stamp Kind and Name at Span Start

Agent integrations have a critical ordering constraint. `_resolve_parent_agent()` in `ddtrace/llmobs/_utils.py` resolves agent attribution when a **child** span activates, not when the parent finishes. Under LIFO nesting the parent span is still open when the child starts — so any field written at span *finish* time is invisible to children that have already been attributed.

**Contract: any integration that produces `kind="agent"` LLMObs spans must stamp kind at span creation, not at finish.**

Direct spans call `_stamp_llmobs_span_kind_at_start()` through `BaseLLMIntegration.trace()`.
Events carrying `llmobs_integration` call it through `LlmTracingSubscriber.on_started`;
subscriber-based integrations must call it in their `SpanCycleEvents.SPAN_STARTED` handler. Extraction classes
customize the two hooks below instead of stamping kind at finish time.

### Hooks to implement for agent spans

**`_llmobs_span_kind(self, operation_id, span, **kwargs) -> Optional[str]`**

Return `"agent"` when the kwargs signal an agent span. The base-class default returns `"agent"` when `kwargs.get("kind") == "agent"`. Override only when the integration uses a different signal (e.g. `operation="agent"` for CrewAI, `interface_type="agent"` for Bedrock). Must be determinable at `trace()` call time — do NOT rely on data available only at finish.

**`_llmobs_agent_name_at_start(self, span, **kwargs) -> Optional[str]`**

Return the agent's LLMObs display name when it can be determined at `trace()` call time. Return `None` to fall back to the span resource name. Override when the integration can resolve the name from kwargs (e.g. Google ADK passes `_dd_agent=<agent_instance>` and the integration reads `agent.name`).

If the integration's `trace()` captures the instance reference before `**kwargs` (e.g. LangGraph),
`_llmobs_agent_name_at_start` cannot reach it through `super().trace()`. Stamp the name directly
in the integration's `trace()` override after calling `super().trace()` and before returning
the span. Follow `ddtrace/llmobs/_integrations/langgraph.py` for this pattern, or
`ddtrace/llmobs/_integrations/google_adk.py` when the instance is available to the agent-name hook.

## Hosted voice conversations: ElevenLabs Agents

The ElevenLabs integration instruments the synchronous and asynchronous Agents
WebSocket loops. Its conversation-local socket proxies observe successful sends
and received messages without changing the audio interface or event subscriptions.
Provider event IDs identify turns; text equality and optional completion events do
not. A turn can contain multiple agent utterances around tools. Reconcile final
text, streaming parts, resends, and corrections by response_id, preserving their
first-observed order, and join their text for the turn. Ignore late parts after
an identified response completes. Events without response_id retain the legacy
single-response fallback. Retain at most 64 response IDs per turn and cap each
response and the combined exported text at 100,000 characters.
Keep a bounded window of open turns for late interrupted transcripts and flush it
on worker exit. Unknown event IDs at or below the interruption boundary cannot
open turns or consume pending input; known interrupted turns still accept late text.
Capture APM and LLMObs parent contexts at startup, including explicit absence.
Create spans under a scoped LLMObs context so both caller contexts remain unchanged;
retain the captured LLMObs snapshot after its parent has finished. Audio payloads belong only to LLM messages, with a shared encoded
budget for input and output. Direct sibling workflow phases retain approximate
transport-based timing and explicitly annotate metadata.ttfa_eligible = false.
Do not infer TTFA or token usage from these estimates. Client tool execution uses
the SDK's actual handler, with explicit parenting and fail-open instrumentation.

Regression fixtures preserve event order and shapes with synthetic audio and text.
Cover default/custom audio interfaces, both SDK loops, ordinary event subscriptions,
typed messages, interruption, errors, parent context, concurrent conversations,
missing completion events, and audio limits.
Cover failed or cancelled external shutdown before the SDK stop signal, plus end
callback failures after that signal; only the former may restore observation.

Optional ElevenLabs vad_score events produce user_speech_activity metadata only
on the matching user speech phase. Version 1 carries source=elevenlabs_vad,
timing=estimated, and at most 128 clip-relative integer-millisecond intervals_ms
pairs. An empty list means observed silence; absent metadata keeps phase-based
colors. Input WAV bytes and placement anchors remain unchanged. Scores enter speech
at 0.5 and leave at 0.35. Low scores cover at most 500 ms of input samples, with
stale delivery gaps and unobserved portions conservatively active. State follows
input-buffer transfers and shutdown extension; overflow omits activity metadata.
Never turn these observations into TTFA or alter customer event subscriptions.


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

Configured model hints belong only to their matching retained event ID; unknown
or finalized context-usage events must not relabel other turns. Tool results share
100,000 characters per turn across handler, outbound, and server observations.
Truncation is reported in metadata while transcript and audio content are retained.

ElevenLabs includes the standard PatchTestCase lifecycle matrix for every wrapped
entry point and checks that the global WebSocket module is left untouched.
Public-SDK smoke tests install fake transport before ordinary patching and use
public session start/end/wait methods. Focused state-machine tests remain useful
for malformed events, late delivery, and buffer limits. Accepted streamed text
observations advance the response end-time fallback; ignored late parts do not.
The provider conversation ID replaces the bootstrap UUID when metadata arrives.

## Duplex audio streams

Use a connection-local adapter to observe successful sends and consumed output
without reading ahead. Preserve input half-close and finish once on output EOF,
close, cancellation, or error. Snapshot caller context and activate it only while
emitting turns. Keep audio retention limits separate from timing validity.

See [Nova Sonic](nova-sonic.md) for its turn boundaries, context propagation,
audio budgets, interruption handling, tool metadata, and cumulative usage rules.
