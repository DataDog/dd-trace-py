---
name: apm-integrations
description: |
  dd-trace-py integration development guide. Use when creating, modifying, or
  debugging contrib integrations in the Python tracer. Covers the patch module
  system, context_with_data, context_with_event (new), registration, testing
  with riot, and common anti-patterns. LLM/AI integrations should use this
  skill for APM-side workflow only; use llmobs-integrations for LLMObs-specific
  lifecycle, extraction, streaming, and VCR guidance. Pin is DEPRECATED.
  Triggers: "dd-trace-py", "ddtrace", "contrib", "integration", "patch.py",
  "trace_handlers", "PATCH_MODULES", "context_with_data", "context_with_event",
  "TracingEvent", "VCR", "cassette", "generative-ai", "LLM integration",
  "riot", "riotfile", "suitespec", "new integration", "wrap", "unwrap".
---

# dd-trace-py APM Integrations

dd-trace-py provides automatic instrumentation for 90+ third-party libraries.
Most integrations intercept library calls to create spans; specialized
integrations may instead publish runtime state to an existing internal listener.

## Architecture

```
Patch Module (ddtrace/contrib/internal/)  -->  Events, Spans, or Tags
  Wraps library methods and uses the integration's established ownership pattern
```

**Standard integrations** use one of these patterns:
- **`context_with_data()` + `trace_handlers.py`** (preferred for existing): Wrappers emit events via `core.context_with_data()`, listeners in `trace_handlers.py` create spans (e.g., botocore, flask, django).
- **`context_with_event()` + `TracingEvent`** (NEW — preferred for new integrations): Typed event-driven pattern using `core.context_with_event()` with `TracingEvent` subclasses and `TracingSubscriber`. Read concrete examples such as `ddtrace/contrib/internal/httpx/patch.py` and `ddtrace/contrib/internal/aiohttp/patch.py`, plus infrastructure in `ddtrace/_trace/events.py` and `ddtrace/_trace/subscribers/`.
- **Raw `core.dispatch()` event** (specialized, no-span integrations only): Use when a runtime integration publishes lifecycle state to an established listener and does not own span creation. Keep the producer decoupled from the consuming product, share an internal event constant when multiple modules publish it, guard hot wrappers with `core.has_listeners()`, and preserve symmetric patch/unpatch behavior. AnyIO and asyncio context-switch publication are the reference implementations. Do not use raw events instead of `TracingEvent` for a new span-producing integration.
- **`Pin` + `tracer.trace()`** (DEPRECATED — do not use in new integrations): Many existing integrations use `Pin.get_from()` + `tracer.trace()` (e.g., redis, kafka, grpc). Do NOT use Pin in new code.

**LLM integrations** still use the APM integration workflow for contrib module layout, patch registration, and APM span tests, but LLMObs-specific span lifecycle and extraction belong in the `llmobs-integrations` skill.

## Key Concepts

- **`_datadog_patch` guard** -- prevents double-patching on repeated `patch()` calls
- **Pin is DEPRECATED** -- many existing integrations still use `Pin.get_from()` and `Pin().onto()`, but do NOT use Pin in new integrations. Prefer `context_with_event` (new) or `context_with_data` (existing)
- **`config._add()`** -- registers integration config at module level, before `patch()` runs
- **`get_version()` / `_supported_versions()`** -- required exports for version detection
- **`PATCH_MODULES`** -- registration dict in `ddtrace/_monkey.py` for `patch_all()` discovery
- **`INTEGRATION_CONFIGS`** -- frozenset in `ddtrace/internal/settings/_config.py`, required for config to work
- **`registry.yaml`** -- dependency names and tested version range in `scripts/integration_registry/registry.yaml`

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

**Always read 1-2 references of the same type before writing or modifying code.**

All patch modules live in `ddtrace/contrib/internal/{name}/`. See
[Reference Integrations](references/reference-integrations.md) for canonical
examples, secondary references, and pattern notes.

---

## LLM Integrations (LLMObs)

Use this APM skill for the shared integration work: contrib package layout,
`patch()` / `unpatch()`, registration in `PATCH_MODULES`,
`scripts/integration_registry/registry.yaml`, config registration, APM span
tests, suitespec plumbing, and release-note/documentation expectations.

Use the **llmobs-integrations** skill for LLM-specific patch patterns,
`LlmRequestEvent`, stream handlers, message/tool/token extraction, metadata
sanitization, LLMObs assertions, and VCR cassette setup.

---

## Implementation Workflow

1. **Investigate** -- Read 1-2 reference integrations of the same type (see tables above)
2. **Create patch module** -- `ddtrace/contrib/internal/{name}/patch.py` with `patch()`, `unpatch()`, `get_version()`
3. **Register** -- Add to `PATCH_MODULES`, `scripts/integration_registry/registry.yaml`, and `INTEGRATION_CONFIGS`
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
