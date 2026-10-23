# LLMObs Integration Guide

Follow the **apm-integrations** skill's [Implementation Guide](../../apm-integrations/references/implementation-guide.md) for patch registration, configuration, and tests. This guide covers LLM-specific behavior.

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
integration. For the standard patched-SDK pattern, `patch()` constructs the integration with
`integration_config=config.<name>` and stores it on `module._datadog_integration`; wrappers
reuse that instance. See `ddtrace/contrib/internal/anthropic/patch.py` for initialization and cleanup.

Do not import the concrete `Span` class (`ddtrace.trace.Span` / `ddtrace._trace.span.Span`) into `ddtrace/llmobs/_integrations/` modules — that recreates a `product:llmobs -> product:tracing` dependency. Type the `span` parameter as `SpanData` from `ddtrace.internal.native._native` instead; it carries the attributes and struct/attribute-tag methods (`_set_attribute`, `_get_struct_tag`, `_set_struct_tag`, etc.) without pulling in `Span`.

`_annotate_llmobs_span_data` (and a few other helpers such as `get_tool_version_from_llm_span`) need `_set_ctx_item`, which `SpanData` doesn't have, so they're typed to accept `_LLMObsSpan` from `ddtrace.llmobs._span` — the single structural `Protocol` all LLMObs code uses for spans. Extend it there when a new call site needs another `Span` member instead of declaring a new per-module protocol. `SpanData` does not satisfy that protocol, so a caller passing a bare `SpanData` there is a type error rather than a silently swallowed exception. The object every integration actually passes at runtime is a full `Span`, which does satisfy the protocol, so annotate your integration's `_llmobs_set_tags` override (and its helpers) with `span: _LLMObsSpan`. That is a valid override of the base class's `span: Span` parameter. Don't type the span as `SpanData` and then `cast(Any, span)` at the call site: that disables the very check the protocol exists for.

Read the current method signatures in `BaseLLMIntegration` and the closest integration.
Implement only the extraction helpers needed by the provider. Register exported classes in
`_INTEGRATION_MODULES` in `ddtrace/llmobs/_integrations/__init__.py` to preserve lazy loading.

## Patch lifecycle (`LlmRequestEvent`)

Standard LLM integrations should use `LlmRequestEvent` from `ddtrace/contrib/_events/llm.py` with `core.context_with_event()`. Read `ddtrace/contrib/internal/anthropic/patch.py` for the current event-based pattern. The patch layer constructs the event, stores the response on `event.response`, and calls `ctx.dispatch_ended_event()`; `ddtrace/_trace/subscribers/llm.py` handles span creation, base tags, LLMObs extraction, errors, and span finish under the hood.

Key points:
- Construct `LlmRequestEvent(..., llmobs_integration=integration, submit_to_llmobs=True, request_kwargs=kwargs, ...)`
- The event/subscriber path owns span creation and finishing; patch wrappers should not call `tracer.trace()`, `integration.trace()`, or create spans directly for standard request spans
- Use `with core.context_with_event(event, dispatch_end_event=False) as ctx:` when streaming or when the wrapper needs to dispatch the ended event manually
- For non-streaming success, set `event.response = resp` and call `ctx.dispatch_ended_event()`
- For errors, call `ctx.dispatch_ended_event(*sys.exc_info())` and re-raise; do not call `span.set_exc_info()` or `span.finish()` directly in the patch wrapper
- LLMObs tag setting is handled by `LlmTracingSubscriber`, not directly in the patch wrapper
- The async variant is identical but uses `async def` / `await`

Some older or specialized integrations still call `integration.trace()` and `integration.llmobs_set_tags()` directly. Use that pattern when modifying an existing integration that already does so, when the closest current reference uses it (for example Google GenAI), or when the behavior requires direct child spans (for example OpenAI MCP tool spans or agent/tool child spans).

## Streaming

Subclass `StreamHandler`/`AsyncStreamHandler` from `ddtrace/internal/utils/streaming.py`, and import `make_traced_stream` from there too. `ddtrace/contrib/internal/stream_handler.py` only re-exports these for backwards compatibility; do not add new imports from it:

- `initialize_chunk_storage()` — set up accumulators for content, usage, role
- `process_chunk(chunk)` — accumulate text, tool blocks, usage from each chunk
- `finalize_stream(exception)` — build the final response and complete the deferred span lifecycle. For `LlmRequestEvent` integrations, set `ctx.event.response` and call `ctx.dispatch_ended_event(...)`; direct-trace integrations may need to call `llmobs_set_tags()` and `span.finish()` themselves. Call this only via `close_stream()` (including eager finish from `process_chunk`, as Claude Agent SDK does). `close_stream()` invokes it at most once. A local idempotency guard inside `finalize_stream()` is still fine if an older path can call it directly.

`TracedStream` / `TracedAsyncStream` invoke `close_stream()` from `__iter__`/`__aiter__` (`finally`), from `StopIteration`/`StopAsyncIteration` on `__next__`/`__anext__`, from `__exit__`/`__aexit__` after the wrapped stream exits, and from `__del__` as a last resort. `__exit__`/`__aexit__` catch `BaseException` so `asyncio.CancelledError` from wrapped cleanup still finalizes. That path matters: a caller that uses `with stream:` and does not exhaust the iterator still finishes the span.

When `__enter__` wraps a stream manager, the parent retains the child wrapper for the `with` body so `__del__` cannot finalize the shared handler early. `on_stream_created` runs before that retain; if it raises, the handler is finalized immediately because Python will not call `__exit__`.

Wire into patch with `make_traced_stream(response, handler)`.

For the agent pattern (tool child spans within streams), see `ddtrace/llmobs/_integrations/claude_agent_sdk.py`.

## Message Extraction

Different libraries structure messages differently — implement only the helpers you need. Read `ddtrace/llmobs/_integrations/anthropic.py` for full code examples.

**Check `_integrations/utils.py` first.** Providers reachable through more than one API share a payload format, and the extraction for it belongs there rather than in each integration. Anthropic content blocks, for example, are parsed by `get_messages_from_anthropic_content()`, `anthropic_tool_call_from_block()`, `anthropic_tool_result_from_block()`, and `get_tool_definitions_from_anthropic_tools()`, which the Anthropic and Amazon Bedrock integrations both call. Converse-format payloads have their own equivalents. Reuse or extend those helpers instead of copying the logic; duplicated parsers drift as providers add content block types.

- `_extract_input_messages(kwargs)` — handle system message, multi-part content blocks, tool results
- `_extract_output_messages(response)` — handle text blocks, tool use blocks, thinking blocks
- `_extract_usage(response)` — map library-specific usage fields (tokens, server-side tool calls) to metric keys
- `_extract_tools(kwargs)` — convert `tools` kwarg to `ToolDefinition` list
- `_extract_audio_parts(...)` — for multimodal audio providers, populate `Message.audio_parts` with `AudioPart` entries containing `mime_type` plus either inline base64 `content` or an `attachment_key`
- `_extract_<lib>_image_source(...)` — same, for `Message.image_parts`. See `anthropic.py`

`MyLibIntegration` owns this extraction logic. Keep provider-specific message, tool, metadata, and usage normalization in the integration subclass rather than in the patch wrapper — except where a payload format is shared with another integration, which belongs in `_integrations/utils.py` as above.

### Inline Media Size Guards

Oversized inline media makes `_writer._truncate_span_event` blank the span's entire input **and** output, not just the offending field. So route media through `format_audio_part_with_guard()` / `format_image_part_with_guard()`, which return `None` and let the caller keep a text marker.

- Pass raw data, not a pre-encoded string, so an oversized payload is rejected before paying to encode it.
- Do non-size rejection (bad MIME, non-ASCII "base64", a `Path`) in the *extractor*. If the guard returns `None` for a non-size reason, the caller's "too large" marker starts lying.
- Each guard bounds one part. Several that each fit can still exceed the event limit — no cross-field budget exists yet.

### Metadata Hygiene

Do not dump raw `kwargs` into LLMObs metadata. Prefer shared helpers such as `get_metadata_from_kwargs()` and provider-specific helpers in `ddtrace/llmobs/_integrations/utils.py` when they apply. They filter provider sentinel defaults such as OpenAI `Omit`/`NotGiven`, serialize SDK objects through `load_data_value()`, and keep metadata limited to supported fields. `_annotate_llmobs_span_data()` coerces metadata keys to strings, but integrations should still avoid sending noisy or high-cardinality raw objects.

### Token Field Mapping

| Library | Input | Output | Cache Fields |
|---------|-------|--------|-------------|
| Anthropic | `input_tokens` | `output_tokens` | `cache_creation_input_tokens`, `cache_read_input_tokens` |
| OpenAI chat | `prompt_tokens` | `completion_tokens` | `prompt_tokens_details.cached_tokens` (already included in input total) |
| Google GenAI | `prompt_token_count` | `candidates_token_count` | `cached_content_token_count` |

Normalize `INPUT_TOKENS_METRIC_KEY` to the total input tokens sent to the model, including cached and non-cached tokens. Providers report this differently: Anthropic reports non-cached `input_tokens` separately from cache read/write input tokens, so add `input_tokens + cache_creation_input_tokens + cache_read_input_tokens`; OpenAI reports prompt/input tokens as the combined total and exposes cached tokens separately in details.

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

`BaseLLMIntegration.trace()` and `LlmTracingSubscriber.on_started` both call `_stamp_llmobs_span_kind_at_start()` automatically. Integrations should not call it directly; instead override the two hooks below.

### Hooks to implement for agent spans

**`_llmobs_span_kind(self, operation_id, span, **kwargs) -> Optional[str]`**

Return `"agent"` when the kwargs signal an agent span. The base-class default returns `"agent"` when `kwargs.get("kind") == "agent"`. Override only when the integration uses a different signal (e.g. `operation="agent"` for CrewAI, `interface_type="agent"` for Bedrock). Must be determinable at `trace()` call time — do NOT rely on data available only at finish.

**`_llmobs_agent_name_at_start(self, span, **kwargs) -> Optional[str]`**

Return the agent's LLMObs display name when it can be determined at `trace()` call time. Return `None` to fall back to the span resource name. Override when the integration can resolve the name from kwargs (e.g. Google ADK passes `_dd_agent=<agent_instance>` and the integration reads `agent.name`).

If the integration's `trace()` captures the instance reference before `**kwargs` (e.g. LangGraph), `_llmobs_agent_name_at_start` cannot reach it through `super().trace()`. In that case, stamp the name directly in the integration's `trace()` override after calling `super()`:

See `ddtrace/llmobs/_integrations/langgraph.py` for an override that captures an instance,
and `ddtrace/llmobs/_integrations/google_adk.py` for the agent-name hook. Read their actual
signatures rather than copying a partial override with an undefined instance.

## Duplex audio streams

Use a connection-local adapter to observe successful sends and consumed output
without reading ahead. Preserve input half-close and finish once on output EOF,
close, cancellation, or error. Snapshot caller context and activate it only while
emitting turns. Keep audio retention limits separate from timing validity.

See [Nova Sonic](nova-sonic.md) for its turn boundaries, context propagation,
audio budgets, interruption handling, tool metadata, and cumulative usage rules.
