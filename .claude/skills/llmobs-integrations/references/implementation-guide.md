# LLMObs Integration Guide

Follow the **apm-integrations** skill's [Implementation Guide](../../apm-integrations/references/implementation-guide.md) for the full step-by-step. This guide expands on the LLM-specific steps.

## Overview

An LLM integration is an APM integration with an extra layer. You do everything in the apm-integrations guide, but:
- **Step 1 (patch module)**: Use `BaseLLMIntegration.trace()` with **LLM events system** (`LlmRequestEvent` (from `ddtrace.contrib._events.llm`))
- **Step 3 (LLMObs integration)**: Create the `BaseLLMIntegration` subclass (this guide)
- **Step 4 (test environment)**: Add `vcrpy: latest` to riotfile, use `tests/llmobs/suitespec.yml`
- **Step 5 (tests)**: Add `test_{name}_llmobs.py` using VCR cassettes and `_expected_llmobs_llm_span_event`

## Step 3 Expanded: Create the Integration Class

Before writing anything, read the existing implementation that most closely matches your library's pattern:

| Pattern | Reference Integration | When to use |
|---------|----------------------|-------------|
| Standard LLM (chat completion) | `anthropic.py` | Library generates text responses from messages |
| Agent runs + tool child spans | `claude_agent_sdk.py` | Library manages an agentic loop with tool calls |
| Embeddings | `openai.py` | Library generates vector embeddings |
| Tool-only calls | `openai.py` | Library wraps discrete tool/function invocations |

Create `ddtrace/llmobs/_integrations/{name}.py`:

```python
from typing import Any, Optional

from ddtrace import Span
from ddtrace.llmobs._constants import INPUT_TOKENS_METRIC_KEY, OUTPUT_TOKENS_METRIC_KEY, TOTAL_TOKENS_METRIC_KEY
from ddtrace.llmobs._integrations.base import BaseLLMIntegration
from ddtrace.llmobs._utils import _annotate_llmobs_span_data
from ddtrace.llmobs.types import Message, ToolCall, ToolDefinition, ToolResult


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
        """Extract and set all LLMObs context items."""
        _annotate_llmobs_span_data(
            span,
            kind="llm",
            model_name=kwargs.get("model", ""),
            model_provider="mylib",
            input_messages=self._extract_input_messages(kwargs),
            output_messages=self._extract_output_messages(response),
            metadata=self._extract_metadata(kwargs),
            metrics=self._extract_usage(response),
            tool_definitions=self._extract_tools(kwargs) or None,
        )
```

Common helpers (implement only what applies):

- `_extract_input_messages(self, kwargs: dict[str, Any]) -> list[Message]` — convert `messages` array to `Message` list; handle system message, multi-part content blocks, tool results
- `_extract_output_messages(self, response: Any) -> list[Message]` — convert response content blocks to `Message` list; handle text, tool_use, thinking blocks
- `_extract_usage(self, response: Any) -> dict[str, int]` — map library token field names to `INPUT_TOKENS_METRIC_KEY` / `OUTPUT_TOKENS_METRIC_KEY` / `TOTAL_TOKENS_METRIC_KEY`
- `_extract_tools(self, kwargs: dict[str, Any]) -> list[ToolDefinition]` — convert `tools` list to `ToolDefinition` list
- `_extract_metadata(self, kwargs: dict[str, Any]) -> dict[str, Any]` — pick scalar request params: `temperature`, `top_p`, `max_tokens`, etc.

Register in `ddtrace/llmobs/_integrations/__init__.py` (import + `__all__` entry).

## Step 1 Expanded: Patch Layer (LLM Events System)

LLM integrations are migrating to the **LLM events system** using `LlmRequestEvent` (from `ddtrace.contrib._events.llm`). Read `ddtrace/contrib/internal/anthropic/patch.py` for the current events-based pattern — the patch layer uses LLM event classes (`LlmRequestEvent`) instead of directly calling `integration.llmobs_set_tags()` in a finally block.

Key points:
- Uses `integration.trace()` — **no direct `tracer.trace()` or span creation**
- LLMObs tag setting is handled by the LLM event, not directly in the patch wrapper
- Streaming delegates to `StreamHandler` subclass instead of finishing span inline
- The async variant is identical but uses `async def` / `await`

## Streaming

Subclass `StreamHandler`/`AsyncStreamHandler` from `ddtrace/llmobs/_integrations/base_stream_handler.py`:

- `initialize_chunk_storage()` — set up accumulators for content, usage, role
- `process_chunk(chunk)` — accumulate text, tool blocks, usage from each chunk
- `finalize_stream(exception)` — build response, call `llmobs_set_tags()`, call `span.finish()`

Wire into patch with `make_traced_stream(response, handler)`.

For the agent pattern (tool child spans within streams), see `ddtrace/llmobs/_integrations/claude_agent_sdk.py`.

## Message Extraction

Different libraries structure messages differently — implement only the helpers you need. Read `ddtrace/llmobs/_integrations/anthropic.py` for full code examples.

- `_extract_input_messages(kwargs)` — handle system message, multi-part content blocks, tool results
- `_extract_output_messages(response)` — handle text blocks, tool use blocks, thinking blocks
- `_extract_usage(response)` — map library-specific token field names to metric keys
- `_extract_tools(kwargs)` — convert `tools` kwarg to `ToolDefinition` list

### Token Field Mapping

| Library | Input | Output | Cache Fields |
|---------|-------|--------|-------------|
| Anthropic | `input_tokens` | `output_tokens` | `cache_creation_input_tokens`, `cache_read_input_tokens` |
| OpenAI | `prompt_tokens` | `completion_tokens` | N/A |
| Google GenAI | `prompt_token_count` | `candidates_token_count` | `cached_content_token_count` |

## LLM-Specific Registration Checklist

In addition to the full checklist in the apm-integrations [Implementation Guide](../../apm-integrations/references/implementation-guide.md):

- [ ] `ddtrace/llmobs/_integrations/{name}.py` — `BaseLLMIntegration` subclass
- [ ] `ddtrace/llmobs/_integrations/__init__.py` — import + `__all__` entry
- [ ] `ddtrace/contrib/internal/{name}/patch.py` — uses LLM events system (see anthropic for pattern)
- [ ] `tests/llmobs/suitespec.yml` — LLMObs test suite entry
- [ ] `riotfile.py` — `"vcrpy": latest` in top-level `pkgs`
- [ ] `docs/index.rst` — add integration to the docs index
