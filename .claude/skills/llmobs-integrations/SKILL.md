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

LLMObs integrations enable Datadog LLM Observability for AI/LLM libraries. They extract model inputs, outputs, token usage, and tool calls from traced spans. This skill should be used in addition to the `apm-integrations` skill.

## Two-Layer Architecture

LLMObs integrations consist of two cooperating layers:

1. **Patch Layer** (`ddtrace/contrib/internal/{name}/patch.py`) -- wraps library functions. Standard request/response LLM integrations construct `LlmRequestEvent` and use `core.context_with_event()` so the LLM tracing subscriber owns span lifecycle and LLMObs tag extraction.
2. **Integration Layer** (`ddtrace/llmobs/_integrations/{name}.py`) -- extends `BaseLLMIntegration`, implements `_set_base_span_tags()` and `_llmobs_set_tags()` to extract and set provider-specific messages, tools, metadata, and token metrics.

Both layers must work together. The patch layer identifies the operation and passes request/response data through the event; the integration layer controls what data is extracted.

## Active Patch Patterns

- **Event-based request spans**: Use `LlmRequestEvent` with `core.context_with_event()` for new standard request/response LLM integrations. Anthropic is the canonical reference. This is the preferred pattern.
- **Direct integration spans**: Some existing or specialized integrations still call `integration.trace()` and `integration.llmobs_set_tags()` directly, especially for child spans, agent/tool spans, or integrations not yet migrated. Google GenAI, OpenAI tool spans, and Claude Agent SDK are useful references.

## Key Files

| Purpose | File |
|---------|------|
| Base LLM integration class | `ddtrace/llmobs/_integrations/base.py` (`BaseLLMIntegration`) |
| Stream handler base classes | `ddtrace/llmobs/_integrations/base_stream_handler.py` (`BaseStreamHandler`, `StreamHandler`, `AsyncStreamHandler`) |
| Shared utilities | `ddtrace/llmobs/_integrations/utils.py` |
| LLMObs annotation helper | `ddtrace/llmobs/_utils.py` (`_annotate_llmobs_span_data`) |
| LLMObs constants | `ddtrace/llmobs/_constants.py` |
| LLMObs types | `ddtrace/llmobs/types.py` (`Message`, `AudioPart`, `ToolCall`, `ToolResult`, `ToolDefinition`) |
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
Extract and annotate all LLMObs fields on the span:

| Field | Description |
|-------------|-------------|
| `kind` | `"llm"` for LLM calls, `"agent"` for agent calls, `"tool"` for tool calls |
| `model_name` | Model identifier (e.g., `"claude-3-sonnet-20240229"`) |
| `model_provider` | Provider name (e.g., `"anthropic"`, `"openai"`) |
| `input_messages` | List of `Message` objects from request |
| `output_messages` | List of `Message` objects from response |
| `metadata` | Dict of sanitized request parameters (temperature, top_p, etc.) |
| `metrics` | Token usage dict with `INPUT_TOKENS_METRIC_KEY`, `OUTPUT_TOKENS_METRIC_KEY`, `TOTAL_TOKENS_METRIC_KEY` |
| `tool_definitions` | List of `ToolDefinition` objects if tools are passed |

Fields are usually set via `_annotate_llmobs_span_data(...)`, not raw `span._set_ctx_items(...)`.

## Key Constraints

- **`submit_to_llmobs=True`** must be set on `LlmRequestEvent` for event-based request spans or passed to `integration.trace()` for direct LLMObs spans
- **`ctx.dispatch_ended_event()`** must run on success and error paths for event-based patch wrappers
- **Streaming** must use `BaseStreamHandler`/`AsyncStreamHandler` -- never consume streams directly
- **Event-based patch wrappers** should not call `span.set_exc_info()`, `span.finish()`, or `integration.llmobs_set_tags()` directly; the tracing subscriber handles that when the event ends
- **Direct integration spans** must keep `integration.llmobs_set_tags()` and span lifecycle handling aligned with the closest current reference
- **Integration instance** must be stored on the module: `module._datadog_integration = MyLibIntegration(integration_config=config.mylib)`

## Message Types

```python
from ddtrace.llmobs.types import AudioPart, Message, ToolCall, ToolResult, ToolDefinition

# Input/output messages
Message(content="text", role="user")
Message(content="response", role="assistant", tool_calls=[...])

# Audio attachments in multimodal messages
AudioPart(mime_type="audio/wav", content="<base64-audio>")
Message(content="", role="user", audio_parts=[...])

# Tool calls (in output messages)
ToolCall(name="get_weather", arguments={"city": "NYC"}, tool_id="toolu_123", type="tool")

# Tool results (in input messages)
ToolResult(result="72F sunny", tool_id="toolu_123", type="tool_result")

# Tool definitions (from request parameters)
ToolDefinition(name="get_weather", description="...", schema={...})
```

## Debugging Quick Tips

- **No LLMObs spans** -- check `submit_to_llmobs=True`, `ctx.dispatch_ended_event()`, and `llmobs_enabled`
- **Wrong messages** -- check message extraction handles multi-part content and tool blocks
- **Wrong tokens** -- check field name mapping (libraries use different names for token counts)
- **Streaming broken** -- verify `BaseStreamHandler` subclass, check `finalize_stream()` dispatches the ended event or finishes direct-trace spans according to the reference pattern
- **DD_TRACE_DEBUG=true** to see patching activity and span creation

See [Failure Modes](references/failure-modes.md) for detailed debugging guide.

## Reference Files

- [Implementation Guide](references/implementation-guide.md) -- LLM-specific steps (references apm-integrations guide for the full workflow)
- [Failure Modes](references/failure-modes.md) -- All 8 failure modes with causes and fixes
- [Testing Guide](references/testing-guide.md) -- LLMObs test patterns, VCR cassettes, suitespec
