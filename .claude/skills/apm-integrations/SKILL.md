---
name: apm-integrations
description: |
  dd-trace-py integration development guide. Use when creating, modifying, or
  debugging contrib integrations in the Python tracer. Covers the patch module
  system, context_with_data, context_with_event (new), BaseLLMIntegration,
  LLMObs integration layer, streaming, message extraction, testing with riot,
  VCR cassettes, and common anti-patterns. Pin is DEPRECATED.
  Triggers: "dd-trace-py", "ddtrace", "contrib", "integration", "patch.py",
  "trace_handlers", "PATCH_MODULES", "context_with_data", "context_with_event",
  "TracingEvent", "BaseLLMIntegration", "integration.trace", "llmobs_set_tags",
  "_llmobs_set_tags", "BaseStreamHandler", "submit_to_llmobs", "LLM span",
  "VCR", "cassette", "anthropic", "openai", "google_genai", "claude_agent_sdk",
  "generative-ai", "LLM integration", "llmobs_enabled", "llmobs", "LLMObs",
  "riot", "riotfile", "suitespec", "new integration", "wrap", "unwrap".
---

# dd-trace-py APM Integrations

dd-trace-py provides automatic tracing for 90+ third-party libraries. Each integration uses monkey-patching via `wrapt` to intercept library calls and create spans.

## Architecture

```
Patch Module (ddtrace/contrib/internal/)  -->  Spans + Tags
  Wraps library methods, creates spans via Pin/tracer or integration.trace()
```

**Standard integrations** use one of these patterns:
- **`context_with_data()` + `trace_handlers.py`** (preferred for existing): Wrappers emit events via `core.context_with_data()`, listeners in `trace_handlers.py` create spans (e.g., botocore, flask, httpx, django).
- **`context_with_event()` + `TracingEvent`** (NEW — preferred for new integrations): Typed event-driven pattern using `core.context_with_event()` with `TracingEvent` subclasses and `TracingSubscriber`. Infrastructure exists in `ddtrace/_trace/events.py` and `ddtrace/_trace/subscribers/`. No contrib integrations use this yet, but new ones should adopt it.
- **`Pin` + `tracer.trace()`** (DEPRECATED — do not use in new integrations): Many existing integrations use `Pin.get_from()` + `tracer.trace()` (e.g., redis, kafka, grpc). Do NOT use Pin in new code.

**LLM integrations** use `BaseLLMIntegration.trace()` and have a two-layer architecture (see LLM section below).

## Key Concepts

- **`_datadog_patch` guard** -- prevents double-patching on repeated `patch()` calls
- **Pin is DEPRECATED** -- many existing integrations still use `Pin.get_from()` and `Pin().onto()`, but do NOT use Pin in new integrations. Prefer `context_with_event` (new) or `context_with_data` (existing)
- **`config._add()`** -- registers integration config at module level, before `patch()` runs
- **`get_version()` / `_supported_versions()`** -- required exports for version detection
- **`PATCH_MODULES`** -- registration dict in `ddtrace/_monkey.py` for `patch_all()` discovery
- **`INTEGRATION_CONFIGS`** -- frozenset in `ddtrace/internal/settings/_config.py`, required for config to work
- **`registry.yaml`** -- dependency names and tested version range in `ddtrace/contrib/integration_registry/`

## Type Annotations

dd-trace-py uses mypy for type checking. All new integration code must pass `hatch run lint:typing`.

**Rules:**
- Every function and method must have full type annotations (parameters + return type)
- `patch() -> None`, `unpatch() -> None`, `get_version() -> str`
- Wrapper functions use the wrapt signature: `def traced_func(wrapped: Callable[..., Any], instance: Any, args: tuple[Any, ...], kwargs: dict[str, Any]) -> Any:`
- Use `Optional[X]` for nullable parameters and return values
- Use parameterized generics: `dict[str, Any]`, `list[str]`, `tuple[Any, ...]` — not bare `dict`, `list`, `tuple`
- Avoid `# type: ignore` unless absolutely necessary (document the reason inline, e.g. `# type: ignore[attr-defined]  # wrapt proxy lacks stubs`)
- Import types from `typing` where needed: `Any`, `Callable`, `Optional`
- Read reference integrations for correct type patterns — match their style

## Reference Integrations

**Always read 1-2 references of the same type before writing or modifying code.**

All patch modules live in `ddtrace/contrib/internal/{name}/`. See [Reference Integrations](references/reference-integrations.md) for secondary examples and detailed notes.

| Category | Canonical Integration | Pattern |
|----------|----------------------|---------|
| cache | `redis/patch.py` | Pin + `context_with_data` |
| cloud-provider | `botocore/patch.py` | Pin + `context_with_data` |
| database | `psycopg/patch.py` | Pin + dbapi |
| faas | `aws_lambda/patch.py` | `wrapping.wrap` + tracer |
| generative-ai | `anthropic/patch.py` | `BaseLLMIntegration.trace()` |
| graphql | `graphql/patch.py` | Pin + `tracer.trace` |
| http-client | `httpx/patch.py` | `context_with_data` |
| http-server | `flask/patch.py` | Pin + `context_with_data` |
| logging | `logging/patch.py` | log correlation (no spans) |
| messaging | `kafka/patch.py` | Pin + `tracer.trace` |
| object-store | `botocore/patch.py` (S3) | Pin + `context_with_data` |
| orchestration | `celery/patch.py` | Pin + `tracer.trace` (signals) |
| rpc | `grpc/patch.py` | Pin + `tracer.trace` |

---

## LLM Integrations (LLMObs)

LLM integrations have a **two-layer architecture**:

1. **Patch Layer** (`ddtrace/contrib/internal/{name}/patch.py`) -- wraps library functions and creates spans. LLM integrations are migrating to the **LLM events system** using `LLMEvent` subclasses (see [PR #16533](https://github.com/DataDog/dd-trace-py/pull/16533)). Read `ddtrace/contrib/internal/anthropic/patch.py` for the current events-based pattern.
2. **Integration Layer** (`ddtrace/llmobs/_integrations/{name}.py`) -- extends `BaseLLMIntegration`, implements `_set_base_span_tags()` and `_llmobs_set_tags()` to extract and set LLMObs metadata. This layer remains the same regardless of the patch pattern.

### LLM Key Files

| Purpose | File |
|---------|------|
| Base LLM integration class | `ddtrace/llmobs/_integrations/base.py` (`BaseLLMIntegration`) |
| Stream handler base classes | `ddtrace/llmobs/_integrations/base_stream_handler.py` |
| LLMObs constants | `ddtrace/llmobs/_constants.py` |
| LLMObs types | `ddtrace/llmobs/types.py` (`Message`, `ToolCall`, `ToolResult`, `ToolDefinition`) |
| Integration registry | `ddtrace/llmobs/_integrations/__init__.py` |

### LLM Reference Integrations

| Provider | Patch File | LLMObs Integration | Tests |
|----------|-----------|-------------------|-------|
| **Anthropic** (canonical) | `contrib/internal/anthropic/patch.py` | `llmobs/_integrations/anthropic.py` | `tests/contrib/anthropic/test_anthropic_llmobs.py` |
| **Claude Agent SDK** (agent pattern) | `contrib/internal/claude_agent_sdk/patch.py` | `llmobs/_integrations/claude_agent_sdk.py` | `tests/contrib/claude_agent_sdk/test_claude_agent_sdk_llmobs.py` |
| OpenAI | `contrib/internal/openai/patch.py` | `llmobs/_integrations/openai.py` | `tests/contrib/openai/test_openai_llmobs.py` |
| Google GenAI | `contrib/internal/google_genai/patch.py` | `llmobs/_integrations/google_genai.py` | `tests/contrib/google_genai/test_google_genai_llmobs.py` |

### LLM Context Items

Subclass `BaseLLMIntegration` and implement `_llmobs_set_tags()` to set:

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

### LLM Key Constraints

- **LLM events system** -- new LLM integrations should use `LLMEvent` subclasses in the patch layer (see anthropic for the events-based pattern). LLMObs tag setting is handled by the event, not directly in the patch wrapper.
- **Streaming** must use `BaseStreamHandler`/`AsyncStreamHandler` -- never consume streams directly
- **Error handling** uses `span.set_exc_info()` in the except block
- **Integration instance** stored on module: `module._datadog_integration = Integration(tracer, config)`

---

## Implementation Workflow

1. **Investigate** -- Read 1-2 reference integrations of the same type (see tables above)
2. **Create patch module** -- `ddtrace/contrib/internal/{name}/patch.py` with `patch()`, `unpatch()`, `get_version()`
3. **Register** -- Add to `PATCH_MODULES`, `registry.yaml`, and `INTEGRATION_CONFIGS`
4. **LLM only: Create integration class** -- Subclass `BaseLLMIntegration` in `ddtrace/llmobs/_integrations/`. See the **llmobs-integrations** skill's [Implementation Guide](../llmobs-integrations/references/implementation-guide.md) for full patterns.
5. **Write tests** -- Add `Venv()` to `riotfile.py`, entries to suitespec, test files
6. **Run tests** -- Use `scripts/run-tests` (see `run-tests` skill)
7. **Verify** -- `ruff check ddtrace/` passes, `patch()`/`unpatch()` cycle works, spans correct

See [Implementation Guide](references/implementation-guide.md) for detailed step-by-step.

## Debugging

- `DD_TRACE_DEBUG=true` to see patching activity and span creation
- Missing spans -- verify `_datadog_patch` guard, check `PATCH_MODULES` entry exists
- Wrong service name -- verify `config._add()` at module level
- **LLM-specific**: see the **llmobs-integrations** skill for failure modes and debugging

### Native Extension Build Issues

If `pip install -e .` fails with Rust compilation errors, `target3.1*` path errors, or stale cached artifacts causing builds to break, clean the cache and retry:

```bash
rm -rf .download_cache .cache src/native/target3.1*
pip install -e .
```

This clears the Rust target directory and pip's download/build caches. Run this before retrying any `pip install` that fails with native extension errors.

## Reference Files

- [Implementation Guide](references/implementation-guide.md) -- Step-by-step new integration (all types)
- [Reference Integrations](references/reference-integrations.md) -- All 13 categories with canonical examples
- [Design Decisions](references/design-decisions.md) -- Why integrations are structured this way
- [Anti-Patterns](references/anti-patterns.md) -- Silent failures and common gotchas
