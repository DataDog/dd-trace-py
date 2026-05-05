---
name: llmobs-integrations
description: |
  dd-trace-py LLMObs integration development guide. Use when creating, modifying,
  or debugging LLMObs integrations for LLM/AI libraries in the Python tracer.
  Covers BaseLLMIntegration, stream handling, message extraction, token counting,
  tool call parsing, and VCR-based testing patterns.
  Triggers: "llmobs", "LLMObs", "BaseLLMIntegration", "llmobs_set_tags",
  "_llmobs_set_tags", "BaseStreamHandler", "submit_to_llmobs", "integration.trace",
  "LLM span", "VCR", "cassette", "anthropic", "openai", "google_genai",
  "claude_agent_sdk", "generative-ai", "LLM integration", "llmobs_enabled".
---

# dd-trace-py LLMObs Integrations

LLMObs integrations enable Datadog LLM Observability for AI/LLM libraries. They extract model inputs, outputs, token usage, and tool calls from traced spans.

## Two-Layer Architecture

LLMObs integrations consist of two cooperating layers:

1. **Patch Layer** (`ddtrace/contrib/internal/{name}/patch.py`) -- wraps library functions, creates spans via `integration.trace(submit_to_llmobs=True)`, and calls `integration.llmobs_set_tags()` in the finally block.
2. **Integration Layer** (`ddtrace/llmobs/_integrations/{name}.py`) -- extends `BaseLLMIntegration`, implements `_set_base_span_tags()` and `_llmobs_set_tags()` to extract and set LLMObs-specific metadata.

Both layers must work together. The patch layer controls span lifecycle; the integration layer controls what data is extracted.

## Key Files

| Purpose | File |
|---------|------|
| Base LLM integration class | `ddtrace/llmobs/_integrations/base.py` (`BaseLLMIntegration`) |
| Stream handler base classes | `ddtrace/llmobs/_integrations/base_stream_handler.py` (`BaseStreamHandler`, `StreamHandler`, `AsyncStreamHandler`) |
| Shared utilities | `ddtrace/llmobs/_integrations/utils.py` |
| LLMObs constants | `ddtrace/llmobs/_constants.py` |
| LLMObs types | `ddtrace/llmobs/types.py` (`Message`, `ToolCall`, `ToolResult`, `ToolDefinition`) |
| Integration registry | `ddtrace/llmobs/_integrations/__init__.py` |

## Reference Integrations

**Always read 1-2 references before writing or modifying LLMObs code.**

| Provider | Patch File | LLMObs Integration | LLMObs Tests |
|----------|-----------|-------------------|--------------|
| **Anthropic** (canonical) | `ddtrace/contrib/internal/anthropic/patch.py` | `ddtrace/llmobs/_integrations/anthropic.py` | `tests/contrib/anthropic/test_anthropic_llmobs.py` |
| **Claude Agent SDK** (latest, agent pattern) | `ddtrace/contrib/internal/claude_agent_sdk/patch.py` | `ddtrace/llmobs/_integrations/claude_agent_sdk.py` | `tests/contrib/claude_agent_sdk/test_claude_agent_sdk_llmobs.py` |
| OpenAI | `ddtrace/contrib/internal/openai/patch.py` | `ddtrace/llmobs/_integrations/openai.py` | `tests/contrib/openai/test_openai_llmobs.py` |
| Google GenAI | `ddtrace/contrib/internal/google_genai/patch.py` | `ddtrace/llmobs/_integrations/google_genai.py` | `tests/contrib/google_genai/test_google_genai_llmobs.py` |

Use **Anthropic** as the canonical reference for standard LLM integrations. Use **Claude Agent SDK** for agent-pattern integrations (agent spans, tool child spans, thinking blocks).

## Abstract Methods to Implement

Subclass `BaseLLMIntegration` and implement:

### `_set_base_span_tags(span, **kwargs)`
Set provider-specific APM tags on the span (e.g., `{name}.request.model`).

### `_llmobs_set_tags(span, args, kwargs, response, operation)`
Extract and set all LLMObs context items on the span:

| Context Item | Description |
|-------------|-------------|
| `SPAN_KIND` | `"llm"` for LLM calls, `"agent"` for agent calls, `"tool"` for tool calls |
| `MODEL_NAME` | Model identifier (e.g., `"claude-3-sonnet-20240229"`) |
| `MODEL_PROVIDER` | Provider name (e.g., `"anthropic"`, `"openai"`) |
| `INPUT_MESSAGES` | List of `Message` objects from request |
| `OUTPUT_MESSAGES` | List of `Message` objects from response |
| `METADATA` | Dict of request parameters (temperature, top_p, etc.) |
| `METRICS` | Token usage dict with `INPUT_TOKENS_METRIC_KEY`, `OUTPUT_TOKENS_METRIC_KEY`, `TOTAL_TOKENS_METRIC_KEY` |
| `TOOL_DEFINITIONS` | List of `ToolDefinition` objects if tools are passed |

Context items are set via `span._set_ctx_items({...})`.

## Key Constraints

- **`submit_to_llmobs=True`** must be passed to `integration.trace()` in the patch layer
- **`integration.llmobs_set_tags()`** must be called in the **finally** block (not try or except)
- **Streaming** must use `BaseStreamHandler`/`AsyncStreamHandler` -- never consume streams directly
- **Error handling** uses `span.set_exc_info()` in the except block, before the finally block
- **Integration instance** must be stored on the module: `module._datadog_integration = Integration(tracer, config)`

## Type Annotations

All LLMObs integration code must pass `hatch run lint:typing` (mypy). Key signatures:

- `_set_base_span_tags(self, span: Span, **kwargs: Any) -> None`
- `_llmobs_set_tags(self, span: Span, args: list[Any], kwargs: dict[str, Any], response: Optional[Any], operation: str) -> None`
- Extraction helpers must have return type annotations: `-> list[Message]`, `-> dict[str, int]`, `-> list[ToolDefinition]`
- Stream handlers: `process_chunk(self, chunk: Any) -> None`, `finalize_stream(self, exception: Optional[BaseException]) -> None`, `initialize_chunk_storage(self) -> None`

See the [Implementation Guide](references/implementation-guide.md) for fully-typed code examples.

## Message Types

```python
from ddtrace.llmobs.types import Message, ToolCall, ToolResult, ToolDefinition

# Input/output messages
Message(content="text", role="user")
Message(content="response", role="assistant", tool_calls=[...])

# Tool calls (in output messages)
ToolCall(name="get_weather", arguments={"city": "NYC"}, tool_id="toolu_123", type="tool")

# Tool results (in input messages)
ToolResult(result="72F sunny", tool_id="toolu_123", type="tool_result")

# Tool definitions (from request parameters)
ToolDefinition(name="get_weather", description="...", schema={...})
```

## Debugging Quick Tips

- **No LLMObs spans** -- check `submit_to_llmobs=True` and `llmobs_set_tags()` in finally block
- **Wrong messages** -- check message extraction handles multi-part content and tool blocks
- **Wrong tokens** -- check field name mapping (libraries use different names for token counts)
- **Streaming broken** -- verify `BaseStreamHandler` subclass, check `finalize_stream()` calls `span.finish()`
- **DD_TRACE_DEBUG=true** to see patching activity and span creation

See [Failure Modes](references/failure-modes.md) for detailed debugging guide.

## Reference Files

- [Implementation Guide](references/implementation-guide.md) -- LLM-specific steps (references apm-integrations guide for the full workflow)
- [Failure Modes](references/failure-modes.md) -- All 8 failure modes with causes and fixes
- [Testing Guide](references/testing-guide.md) -- LLMObs test patterns, VCR cassettes, suitespec
