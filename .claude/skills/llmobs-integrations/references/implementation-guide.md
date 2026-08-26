# LLMObs Integration Guide

Follow the **apm-integrations** skill's [Implementation Guide](../../apm-integrations/references/implementation-guide.md) for the full step-by-step. This guide expands on the LLM-specific steps.

## Design: Two-Layer Architecture

LLM integrations use `BaseLLMIntegration` as a second layer on top of a standard APM integration:
- Patch code creates a subclass instance and stores it on the module: `module._datadog_integration = MyLibIntegration(integration_config=config.mylib)`
- Standard request/response patch code constructs `LlmRequestEvent` and uses `core.context_with_event()`; `LlmTracingSubscriber` manages span lifecycle and calls `integration.llmobs_set_tags()` when the event ends
- The `BaseLLMIntegration` subclass in `ddtrace/llmobs/_integrations/` handles provider-specific message, token, metadata, and tool extraction
- Some existing or specialized integrations still call `integration.trace()` directly for direct child spans; follow the closest current reference before using that pattern

This separation keeps APM patching decoupled from LLMObs data extraction.

## Overview

An LLM integration is an APM integration with an extra layer. You do everything in the apm-integrations guide, but:
- **Step 1 (patch module)**: Use `LlmRequestEvent` with `core.context_with_event()` for standard request/response LLM integrations
- **Step 3 (LLMObs integration)**: Create the `BaseLLMIntegration` subclass that handles provider-specific message, tool, and token extraction (this guide)
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

```python
from typing import Any, Optional

from ddtrace.trace import Span
from ddtrace.llmobs._constants import INPUT_TOKENS_METRIC_KEY, OUTPUT_TOKENS_METRIC_KEY, TOTAL_TOKENS_METRIC_KEY
from ddtrace.llmobs._integrations.base import BaseLLMIntegration
from ddtrace.llmobs._utils import _annotate_llmobs_span_data
from ddtrace.llmobs.types import AudioPart, Message, ToolCall, ToolDefinition, ToolResult


class MyLibIntegration(BaseLLMIntegration):
    _integration_name: str = "mylib"

    def _set_base_span_tags(self, span: Span, **kwargs: Any) -> None:
        """Set APM tags on span."""
        span.set_tag_str("mylib.request.model", kwargs.get("model", ""))

    def _llmobs_set_tags(
        self,
        span: Span,
        args: list[Any],
        kwargs: dict[str, Any],
        response: Optional[Any] = None,
        operation: str = "",
    ) -> None:
        """Extract and annotate all LLMObs fields."""
        tools = self._extract_tools(kwargs)
        _annotate_llmobs_span_data(
            span,
            kind="llm",
            model_name=kwargs.get("model", ""),
            model_provider="mylib",
            input_messages=self._extract_input_messages(kwargs),
            output_messages=self._extract_output_messages(response),
            metadata=self._extract_metadata(kwargs),
            metrics=self._extract_usage(response),
            tool_definitions=tools or None,
        )
```

Common helpers (implement only what applies):

- `_extract_input_messages(self, kwargs: dict[str, Any]) -> list[Message]` — convert `messages` array to `Message` list; handle system message, multi-part content blocks, tool results
- `_extract_output_messages(self, response: Any) -> list[Message]` — convert response content blocks to `Message` list; handle text, tool_use, thinking blocks
- `_extract_usage(self, response: Any) -> dict[str, int]` — map library token field names to `INPUT_TOKENS_METRIC_KEY` / `OUTPUT_TOKENS_METRIC_KEY` / `TOTAL_TOKENS_METRIC_KEY`
- `_extract_tools(self, kwargs: dict[str, Any]) -> list[ToolDefinition]` — convert `tools` list to `ToolDefinition` list
- `_extract_metadata(self, kwargs: dict[str, Any]) -> dict[str, Any]` — pick scalar request params: `temperature`, `top_p`, `max_tokens`, etc.

Register in `ddtrace/llmobs/_integrations/__init__.py` (import + `__all__` entry).

## Step 1 Expanded: Patch Layer (`LlmRequestEvent`)

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

Subclass `StreamHandler`/`AsyncStreamHandler` from `ddtrace/llmobs/_integrations/base_stream_handler.py`:

- `initialize_chunk_storage()` — set up accumulators for content, usage, role
- `process_chunk(chunk)` — accumulate text, tool blocks, usage from each chunk
- `finalize_stream(exception)` — build the final response and complete the deferred span lifecycle. For `LlmRequestEvent` integrations, set `ctx.event.response` and call `ctx.dispatch_ended_event(...)`; direct-trace integrations may need to call `llmobs_set_tags()` and `span.finish()` themselves.

Wire into patch with `make_traced_stream(response, handler)`.

For the agent pattern (tool child spans within streams), see `ddtrace/llmobs/_integrations/claude_agent_sdk.py`.

## Message Extraction

Different libraries structure messages differently — implement only the helpers you need. Read `ddtrace/llmobs/_integrations/anthropic.py` for full code examples.

- `_extract_input_messages(kwargs)` — handle system message, multi-part content blocks, tool results
- `_extract_output_messages(response)` — handle text blocks, tool use blocks, thinking blocks
- `_extract_usage(response)` — map library-specific token field names to metric keys
- `_extract_tools(kwargs)` — convert `tools` kwarg to `ToolDefinition` list
- `_extract_audio_parts(...)` — for multimodal audio providers, populate `Message.audio_parts` with `AudioPart` entries containing `mime_type` plus either inline base64 `content` or an `attachment_key`
- `_extract_<lib>_image_source(...)` — same, for `Message.image_parts`. See `anthropic.py`

`MyLibIntegration` owns this extraction logic. Keep provider-specific message, tool, metadata, and token normalization in the integration subclass rather than in the patch wrapper.

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
def _llmobs_agent_name_at_start(self, span: Span, **kwargs: Any) -> Optional[str]:
    agent = kwargs.get("_dd_agent")
    return getattr(agent, "name", None) if agent else None
```

## LLM-Specific Registration Checklist

In addition to the full checklist in the apm-integrations [Implementation Guide](../../apm-integrations/references/implementation-guide.md):

- [ ] `ddtrace/llmobs/_integrations/{name}.py` — `BaseLLMIntegration` subclass
- [ ] `ddtrace/llmobs/_integrations/__init__.py` — import + `__all__` entry
- [ ] `ddtrace/contrib/internal/{name}/patch.py` — uses `LlmRequestEvent` + `core.context_with_event()` for standard LLM request spans (see anthropic for pattern)
- [ ] `tests/llmobs/suitespec.yml` — LLMObs test suite entry
- [ ] Test dependencies match the suite style; include `vcrpy` only when cassette replay is used
- [ ] `docs/index.rst` — add integration to the docs index
