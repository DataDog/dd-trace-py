# LLMObs Integration Guide

Follow the **apm-integrations** skill's [Implementation Guide](../../apm-integrations/references/implementation-guide.md) for the full step-by-step. This guide expands on the LLM-specific steps.

## Design: Two-Layer Architecture

LLM integrations use `BaseLLMIntegration` as a second layer on top of a standard APM integration:
- Patch code in `ddtrace/contrib/internal/{name}/` constructs an `LlmRequestEvent` and enters it with `core.context_with_event()`. It does not import LLMObs or create the integration.
- `LlmTracingSubscriber` (`ddtrace/_trace/subscribers/llm.py`) manages the span lifecycle and dispatches `LlmEvents` (`SPAN_STARTING`, `SPAN_STARTED`, `SPAN_FINISHING`) with the `ExecutionContext`
- LLMObs subscribers in `ddtrace/llmobs/_contrib/{name}/` handle those events: they lazily build the `BaseLLMIntegration` subclass, set LLMObs base tags at span start, and call `integration.llmobs_set_tags()` at span finish (see Step 2 below)
- The `BaseLLMIntegration` subclass in `ddtrace/llmobs/_integrations/` handles provider-specific message, usage, metadata, and tool extraction

This separation keeps contrib code from depending on LLMObs: the patch layer only emits events, and LLMObs decides what to do with them.

Legacy integrations that predate this pattern still create the integration in patch code, store it on the module (`module._datadog_integration = MyLibIntegration(integration_config=config.mylib)`), and pass it as `LlmRequestEvent(llmobs_integration=...)`; `LlmTracingSubscriber` then calls it directly. Some specialized integrations also call `integration.trace()` directly for child spans. Don't copy either pattern into a new integration, because both make contrib import LLMObs.

## Overview

An LLM integration is an APM integration with an extra layer. You do everything in the apm-integrations guide, but:
- **Step 1 (patch module)**: Use `LlmRequestEvent` with `core.context_with_event()` for standard request/response LLM integrations
- **Step 2 (LLMObs subscribers)**: Add `ddtrace/llmobs/_contrib/{name}/` subscribers, with `listen()`/`unlisten()` in its `__init__.py`, and declare that module under the `ddtrace.integration_hooks` entry-point group in `pyproject.toml`
- **Step 3 (LLMObs integration)**: Create the `BaseLLMIntegration` subclass that handles provider-specific message, tool, and usage extraction (this guide)
- **Step 4 (test environment)**: Use `tests/llmobs/suitespec.yml`; add `vcrpy` only when the suite uses vcrpy cassettes and follow nearby version pins
- **Step 5 (tests)**: Add `test_{name}_llmobs.py` in addition to the APM `test_{name}.py`, using the right transport pattern for the integration and `assert_llmobs_span_data(_get_llmobs_data_metastruct(span), ...)`

## Step 3 Expanded: Create the Integration Class

Before writing anything, read the existing implementation that most closely matches your library's pattern:

| Pattern | Reference Integration | When to use |
|---------|----------------------|-------------|
| Standard LLM (chat completion) | `anthropic.py` | Library generates text responses from messages |
| Agent runs + tool child spans | `claude_agent_sdk.py` | Library manages an agentic loop with tool calls |
| Embeddings | `openai.py` | Library generates vector embeddings |

Create `ddtrace/llmobs/_integrations/{name}.py`:

Do not import the concrete `Span` class (`ddtrace.trace.Span` / `ddtrace._trace.span.Span`) into `ddtrace/llmobs/_integrations/` modules — that recreates a `product:llmobs -> product:tracing` dependency. Type the `span` parameter as `SpanData` from `ddtrace.internal.native._native` instead; it carries the attributes and struct/attribute-tag methods (`_set_attribute`, `_get_struct_tag`, `_set_struct_tag`, etc.) without pulling in `Span`.

`_annotate_llmobs_span_data` (and a few other helpers such as `get_tool_version_from_llm_span`) need `_set_ctx_item`, which `SpanData` doesn't have, so they're typed to accept `_LLMObsSpan` from `ddtrace.llmobs._span` — the single structural `Protocol` all LLMObs code uses for spans. Extend it there when a new call site needs another `Span` member instead of declaring a new per-module protocol. `SpanData` does not satisfy that protocol, so a caller passing a bare `SpanData` there is a type error rather than a silently swallowed exception. The object every integration actually passes at runtime is a full `Span`, which does satisfy the protocol, so annotate your integration's `_llmobs_set_tags` override (and its helpers) with `span: _LLMObsSpan`. That is a valid override of the base class's `span: Span` parameter. Don't type the span as `SpanData` and then `cast(Any, span)` at the call site: that disables the very check the protocol exists for.

```python
from typing import Any, Optional

from ddtrace.internal.native._native import SpanData
from ddtrace.llmobs._constants import INPUT_TOKENS_METRIC_KEY, OUTPUT_TOKENS_METRIC_KEY, TOTAL_TOKENS_METRIC_KEY
from ddtrace.llmobs._integrations.base import BaseLLMIntegration
from ddtrace.llmobs._span import _LLMObsSpan
from ddtrace.llmobs._utils import _annotate_llmobs_span_data
from ddtrace.llmobs.types import AudioPart, Message, ToolCall, ToolDefinition, ToolResult


class MyLibIntegration(BaseLLMIntegration):
    _integration_name: str = "mylib"

    def _set_base_span_tags(self, span: SpanData, **kwargs: Any) -> None:
        """Set APM tags on span."""
        span._set_attribute("mylib.request.model", kwargs.get("model", ""))

    def _llmobs_set_tags(
        self,
        span: _LLMObsSpan,
        args: list[Any],
        kwargs: dict[str, Any],
        response: Optional[Any] = None,
        operation: str = "",
    ) -> None:
        """Extract and annotate all LLMObs fields."""
        tools = self._extract_tools(kwargs)
        metadata = self._extract_metadata(kwargs)
        # Gate on the response, not span.error: an errored span can still carry a valid
        # response. Merge so response-derived keys never clobber the request params.
        if response is not None:
            metadata.update(self._extract_response_metadata(response))
        _annotate_llmobs_span_data(
            span,
            kind="llm",
            model_name=kwargs.get("model", ""),
            model_provider="mylib",
            input_messages=self._extract_input_messages(kwargs),
            output_messages=self._extract_output_messages(response),
            metadata=metadata,
            metrics=self._extract_usage(response),
            tool_definitions=tools or None,
        )
```

Common helpers (implement only what applies):

- `_extract_input_messages(self, kwargs: dict[str, Any]) -> list[Message]` — convert `messages` array to `Message` list; handle system message, multi-part content blocks, tool results
- `_extract_output_messages(self, response: Any) -> list[Message]` — convert response content blocks to `Message` list; handle text, tool_use, thinking blocks
- `_extract_usage(self, response: Any) -> dict[str, int]` — map library usage fields to metric keys: token counts to `INPUT_TOKENS_METRIC_KEY` / `OUTPUT_TOKENS_METRIC_KEY` / `TOTAL_TOKENS_METRIC_KEY`, and provider-billed server-side tool calls as described in Server-Side Tool Usage below
- `_extract_tools(self, kwargs: dict[str, Any]) -> list[ToolDefinition]` — convert `tools` list to `ToolDefinition` list
- `_extract_metadata(self, kwargs: dict[str, Any]) -> dict[str, Any]` — pick scalar request params: `temperature`, `top_p`, `max_tokens`, etc.
- `_extract_response_metadata(self, response: Any) -> dict[str, Any]` — pick scalar params the provider reports on the *response* and that `metrics`/`output_messages` do not already cover. The stop reason is the established case: record it as `finish_reason`, omit the key when the provider reports none, and comma-join per-choice reasons in choice order when a request returns multiple choices so the value stays a single string. See the Response Metadata section of `SKILL.md` for the per-provider sources and `_openai_finish_reason_metadata()` in `_integrations/utils.py` for the comma-join helper.

Register in `ddtrace/llmobs/_integrations/__init__.py` (import + `__all__` entry).

## Step 1 Expanded: Patch Layer (`LlmRequestEvent`)

Standard LLM integrations should use `LlmRequestEvent` from `ddtrace/contrib/_events/llm.py` with `core.context_with_event()`. Read `ddtrace/contrib/internal/anthropic/patch.py` for the current event-based pattern. The patch layer constructs the event, stores the response on `event.response`, and calls `ctx.dispatch_ended_event()`; `ddtrace/_trace/subscribers/llm.py` handles span creation, errors, and span finish, and dispatches `LlmEvents` so LLMObs subscribers can add base tags and LLMObs data.

Key points:
- Construct `LlmRequestEvent(..., submit_to_llmobs=True, request_kwargs=kwargs, ...)` and leave `llmobs_integration` unset
- Set APM tags the contrib owns (for example `{name}.request.model`) through the event's `tags`, not in the `BaseLLMIntegration`
- Shared stream helpers live in `ddtrace/contrib/internal/stream_handler.py`; pass `None` as the integration when the contrib has none
- The event/subscriber path owns span creation and finishing; patch wrappers should not call `tracer.trace()`, `integration.trace()`, or create spans directly for standard request spans
- Use `with core.context_with_event(event, dispatch_end_event=False) as ctx:` when streaming or when the wrapper needs to dispatch the ended event manually
- For non-streaming success, set `event.response = resp` and call `ctx.dispatch_ended_event()`
- For errors, call `ctx.dispatch_ended_event(*sys.exc_info())` and re-raise; do not call `span.set_exc_info()` or `span.finish()` directly in the patch wrapper
- LLMObs tag setting is handled by the LLMObs subscribers, not directly in the patch wrapper

## Step 2 Expanded: LLMObs Subscribers

Read `ddtrace/llmobs/_contrib/anthropic/` for the pattern. `LlmTracingSubscriber` dispatches three events with the `ExecutionContext`. They are shared by every LLM integration, so each handler returns early unless `ctx.event.component` matches:
- `LlmEvents.SPAN_STARTING` runs before the span exists. This is the only point where `event.span_type` can still be set to `SpanTypes.LLM`, which `LLMObs._on_span_start` needs at creation.
- `LlmEvents.SPAN_STARTED` runs after the span is created. Use it for `_set_base_span_tags()`, `_annotate_integration_tag()`, and `_stamp_llmobs_span_kind_at_start()`.
- `LlmEvents.SPAN_FINISHING` runs before the span is finished. Call `integration.llmobs_set_tags()` here.

Subscribers set `auto_register = False`. Register them from `listen()` in `ddtrace/llmobs/_contrib/{name}/__init__.py`, and unregister them from `unlisten()` -- except the `SPAN_FINISHING` subscriber, which stays registered so requests and deferred streams already in flight at unpatch time still get finalized. Declare the module in `pyproject.toml`:

```toml
[project.entry-points.'ddtrace.integration_hooks']
{name} = "ddtrace.llmobs._contrib.{name}"
```

The contrib's `patch()` calls `integration_hooks.on_patch("{name}")` after wrapping, and `unpatch()` calls `integration_hooks.on_unpatch("{name}")` before unwrapping. The hook module is only imported when that integration is patched, so the contrib never imports LLMObs, and registration works for `ddtrace-run`, `import ddtrace.auto` and direct `patch()` calls alike. Do not add registration hooks to `ddtrace.patch()` or `ddtrace/_monkey.py`. Entry points are read from installed package metadata, so rebuild dev environments (`scripts/run-tests -r`) after adding one.
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
| OpenAI | `prompt_tokens` | `completion_tokens` | N/A |
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

```python
def trace(self, operation_id, submit_to_llmobs=False, **kwargs):
    span = super().trace(operation_id, submit_to_llmobs=submit_to_llmobs, **kwargs)
    # instance captured before **kwargs; stamp name after super() has written the kind
    if submit_to_llmobs and self.llmobs_enabled and kwargs.get("kind") == "agent":
        agent_name = getattr(instance, "name", None) if instance else None
        if agent_name:
            _annotate_llmobs_span_data(span, name=agent_name)
    return span
```

### Example: Google ADK

Patch layer passes the agent object via a private kwarg:
```python
span = integration.trace(
    "%s.%s" % (instance.__class__.__name__, wrapped.__name__),
    kind="agent",
    submit_to_llmobs=True,
    _dd_agent=agent,   # <-- agent instance available at trace() call time
)
```

Integration overrides the hook:
```python
def _llmobs_agent_name_at_start(self, span: SpanData, **kwargs: Any) -> Optional[str]:
    agent = kwargs.get("_dd_agent")
    return getattr(agent, "name", None) if agent else None
```

## LLM-Specific Registration Checklist

In addition to the full checklist in the apm-integrations [Implementation Guide](../../apm-integrations/references/implementation-guide.md):

- [ ] `ddtrace/llmobs/_integrations/{name}.py` — `BaseLLMIntegration` subclass
- [ ] `ddtrace/llmobs/_integrations/__init__.py` — import + `__all__` entry
- [ ] `ddtrace/contrib/internal/{name}/patch.py` — uses `LlmRequestEvent` + `core.context_with_event()` for standard LLM request spans, with no `ddtrace.llmobs` imports (see anthropic for pattern)
- [ ] `ddtrace/llmobs/_contrib/{name}/` — `LlmEvents` subscribers, registered through the `ddtrace.integration_hooks` entry point in `pyproject.toml`
- [ ] `tests/llmobs/suitespec.yml` — LLMObs test suite entry
- [ ] Test dependencies match the suite style; include `vcrpy` only when cassette replay is used
- [ ] `docs/index.rst` — add integration to the docs index

## Duplex audio streams

Use a connection-local adapter to observe successful sends and consumed output
without reading ahead. Preserve input half-close and finish once on output EOF,
close, cancellation, or error. Snapshot caller context and activate it only while
emitting turns. Keep audio retention limits separate from timing validity.

See [Nova Sonic](nova-sonic.md) for its turn boundaries, context propagation,
audio budgets, interruption handling, tool metadata, and cumulative usage rules.
