---
name: apm-integrations
description: |
  dd-trace-py integration development guide. Use when creating, modifying, or
  debugging contrib integrations in the Python tracer. Covers the patch module
  system, context_with_data, context_with_event (new), BaseLLMIntegration,
  LLMObs integration layer, streaming, message extraction, testing with riot,
  VCR cassettes, and common anti-patterns.
  Triggers: "dd-trace-py", "ddtrace", "contrib", "integration", "patch.py",
  "trace_handlers", "PATCH_MODULES", "context_with_data", "context_with_event",
  "TracingEvent", "BaseLLMIntegration", "llmobs_set_tags",
  "_llmobs_set_tags", "BaseStreamHandler", "submit_to_llmobs", "LLM span",
  "VCR", "cassette", "anthropic", "openai", "google_genai", "claude_agent_sdk",
  "generative-ai", "LLM integration", "llmobs_enabled", "llmobs", "LLMObs",
  "riot", "riotfile", "suitespec", "new integration", "wrap", "unwrap".
---

# dd-trace-py APM Integrations

dd-trace-py provides automatic tracing for 90+ third-party libraries. Each integration uses monkey-patching to intercept library calls and create spans. Most use `wrapt.wrap_function_wrapper`; some use bytecode-level wrapping (`ddtrace/internal/wrapping/`).

## Architecture

```
Patch Module (ddtrace/contrib/internal/)  -->  Spans + Tags
  Wraps library methods, creates events.
```

**Standard integrations** use one of these patterns:
- **`context_with_data()` + `trace_handlers.py`** (preferred for existing): Wrappers emit events via `core.context_with_data()`, listeners in `trace_handlers.py` create spans (e.g., botocore, flask, httpx, django). We are trying to move away from this pattern in favor of the below.
- **`context_with_event()` + `TracingEvent`** (NEW — preferred for new integrations): Typed event-driven pattern using `core.context_with_event()` with `TracingEvent` subclasses and `TracingSubscriber`. Infrastructure exists in `ddtrace/_trace/events.py` and `ddtrace/_trace/subscribers/`. No contrib integrations use this yet, but new ones should adopt it.
- **`Pin` + `tracer.trace()`** (DEPRECATED — do not use in new integrations): Many existing integrations use `Pin.get_from()` + `tracer.trace()` (e.g., kafka, celery, graphql). Do NOT use Pin in new code.

**LLM integrations** use `BaseLLMIntegration.trace()` and have a two-layer architecture (see LLM section below).

## Key Concepts

- **`_datadog_patch` guard** -- prevents double-patching on repeated `patch()` calls
- **`config._add()`** -- registers integration config at module level, before `patch()` runs
- **`get_version()` / `_supported_versions()`** -- required exports for version detection
- **`PATCH_MODULES`** -- registration dict in `ddtrace/_monkey.py` for `patch_all()` discovery
- **`INTEGRATION_CONFIGS`** -- frozenset in `ddtrace/internal/settings/_config.py`, required for config to work
- **`registry.yaml`** -- dependency names and tested version range in `ddtrace/contrib/integration_registry/`

## Type Annotations

dd-trace-py uses mypy for type checking. All new integration code must pass the **lint** skill typing check (`scripts/lint typing`).

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

**Read 1-2 references of the same type before writing or modifying code.**

**Read 1-2 references of the same type before writing or modifying code** (skip if it's a genuinely novel category with no comparable integration). See [Reference Integrations](references/reference-integrations.md) for all 13 categories with canonical examples, secondary references, and pattern notes.


---

## LLM Integrations (LLMObs)

LLM integrations have a two-layer architecture:
1. **Patch Layer** (`ddtrace/contrib/internal/{name}/patch.py`) -- wraps library functions, creates spans using the LLM events system (`LLMEvent` subclasses). Read `ddtrace/contrib/internal/anthropic/patch.py` for the canonical pattern.
2. **Integration Layer** (`ddtrace/llmobs/_integrations/{name}.py`) -- extends `BaseLLMIntegration`, implements `_set_base_span_tags()` and `_llmobs_set_tags()`.

For the full LLM guide — `BaseLLMIntegration`, stream handling, message extraction, token counting, and VCR testing — see the **llmobs-integrations** skill.

See the **llmobs-integrations** skill for reference integrations and full implementation details.

---

## Implementation Workflow

1. **Investigate** -- Read 1-2 reference integrations of the same type (see tables above)
2. **Create patch module** -- `ddtrace/contrib/internal/{name}/patch.py` with `patch()`, `unpatch()`, `get_version()`
3. **Register** -- Add to `PATCH_MODULES`, `registry.yaml`, and `INTEGRATION_CONFIGS`
4. **LLM only: Create integration class** -- Subclass `BaseLLMIntegration` in `ddtrace/llmobs/_integrations/`. See the **llmobs-integrations** skill's [Implementation Guide](../llmobs-integrations/references/implementation-guide.md) for full patterns.
5. **Write tests** -- Add `Venv()` to `riotfile.py`, entries to suitespec, test files
6. **Run tests** -- Use the **run-tests** skill
7. **Lint** -- Use the **lint** skill

See [Implementation Guide](references/implementation-guide.md) for detailed step-by-step.

## Debugging

- `DD_TRACE_DEBUG=true` to see patching activity and span creation
- Missing spans -- verify `_datadog_patch` guard, check `PATCH_MODULES` entry exists
- Wrong service name -- verify `config._add()` at module level
- **LLM-specific**: see the **llmobs-integrations** skill for failure modes and debugging

### Native Extension Build Issues

If `pip install -e .` fails with Rust compilation errors, `target3.1*` path errors, or stale cached artifacts causing builds to break, refer to 
[Troubleshooting](../../../docs/troubleshooting.rst)

This clears the Rust target directory and pip's download/build caches. Run this before retrying any `pip install` that fails with native extension errors.

## Reference Files

- [Implementation Guide](references/implementation-guide.md) -- Step-by-step new integration (all types)
- [Reference Integrations](references/reference-integrations.md) -- All 13 categories with canonical examples
- [Design Decisions](references/design-decisions.md) -- Why integrations are structured this way
- [Anti-Patterns](references/anti-patterns.md) -- Silent failures and common gotchas
