---
name: apm-integrations
description: |
  dd-trace-py integration development guide. Use when creating, modifying, or
  debugging contrib integrations in the Python tracer. Covers the patch module
  system, typed events and subscribers (the default for new instrumentation), registration, testing
  through the repository test runner, and common anti-patterns. LLM/AI integrations should use this
  skill for APM-side workflow only; use llmobs-integrations for LLMObs-specific
  lifecycle, extraction, streaming, and VCR guidance. Pin is DEPRECATED.
  Triggers: "dd-trace-py", "ddtrace", "contrib", "integration", "patch.py",
  "trace_handlers", "PATCH_MODULES", "context_with_data", "context_with_event",
  "TracingEvent", "VCR", "cassette", "generative-ai", "LLM integration",
  "run-tests", "suitespec", "new integration", "wrap", "unwrap",
  "manifest.yaml", "integration manifest".
---

# dd-trace-py APM Integrations

dd-trace-py integrations use monkey-patching via `wrapt` to intercept library calls
and expose operations through typed events. Product subscribers implement tracing
and other product behavior.

**Use the events API for new integrations and new instrumentation.** Read
[events-api](../events-api/SKILL.md) before designing the event contract or
implementing handlers. Search for reusable events and subscribers first.

## Architecture

```
Patch Module (ddtrace/contrib/internal/)
  Wraps library methods, extracts inputs, dispatches typed events, populates results
    --> Events (ddtrace/contrib/, close to their source)
      --> Subscribers (in the owning product)
        Create spans, set tags, or perform other product behavior
```

**The target architecture is product-agnostic integrations with no tracing code.**
Use `core.context_with_event()` for operation lifecycles and
`core.dispatch_event()` for point-in-time notifications. Events are typed
transports with minimal data preparation; subscribers own product behavior.
Reuse or compose existing contracts and subscribers before creating new ones.
For tracing, use `TracingEvent` and `TracingSubscriber` where their contracts fit.

Existing integrations also contain **legacy patterns**:
- **`context_with_data()` + `trace_handlers.py`**: Untyped context payloads and centralized handlers remain in existing code. Do not choose this pattern for new instrumentation or add duplicate handlers when a shared subscriber can own the behavior.
- **`Pin` + `tracer.trace()`** (DEPRECATED): Direct tracing remains in existing integrations. Do not introduce it in new code.

When fixing existing integrations, preserve observable behavior and keep migration
scope appropriate to the task. A focused fix does not require migrating the whole
integration. Existing code and reference examples may contain transitional
patterns; use the events-api skill as the architectural guidance for new work.

**LLM integrations** still use the APM integration workflow for contrib module layout, patch registration, and APM span tests, but LLMObs-specific span lifecycle and extraction belong in the `llmobs-integrations` skill.

## Key Concepts

- **`_datadog_patch` guard** -- prevents double-patching on repeated `patch()` calls
- **Events and subscribers** -- integrations supply typed operation data; product subscribers own behavior. Subscribers register when imported, so wire their modules through the owning product's initialization. Inherited hooks run automatically; do not duplicate them with `super()` calls. See [events-api](../events-api/SKILL.md) for the full contract and lifecycle rules.
- **Pin is DEPRECATED** -- existing uses are legacy; use the events API for new instrumentation.
- **`config._add()`** -- registers integration config at module level, before `patch()` runs
- **`get_version()` / `_supported_versions()`** -- required exports for version detection
- **`PATCH_MODULES`** -- registration dict in `ddtrace/_monkey.py` for `patch_all()` discovery
- **`INTEGRATION_CONFIGS`** -- frozenset in `ddtrace/internal/settings/_config.py`, required for config to work
- **`registry.yaml`** -- dependency names and tested version range in `scripts/integration_registry/registry.yaml`
- **`{name}.manifest.yaml`** -- declarative metadata (packages, categories, systems) next to each patch module. Required for every new integration; existing ones rarely change. See "What is an integration manifest?" in [Contributing Integrations](../../../docs/contributing-integrations.rst)

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

1. **Investigate and design the contract** -- Read the [events-api skill](../events-api/SKILL.md) and 1-2 reference integrations of the same type. Find shared events/subscribers to reuse or compose, identify the owning product for behavior, and define start/result/completion data before implementing wrappers. Do not copy legacy tracing patterns from the references.
2. **Create patch module** -- `ddtrace/contrib/internal/{name}/patch.py` with `patch()`, `unpatch()`, `get_version()`
3. **Register** -- Add to `PATCH_MODULES`, `scripts/integration_registry/registry.yaml`, and `INTEGRATION_CONFIGS`, and add `{name}.manifest.yaml`
4. **LLM only: Create integration class** -- Subclass `BaseLLMIntegration` in `ddtrace/llmobs/_integrations/`. See the **llmobs-integrations** skill's [Implementation Guide](../llmobs-integrations/references/implementation-guide.md) for full patterns.
5. **Write tests** -- Add a suitespec environment and test files
6. **Run tests** -- Use the **run-tests** skill
7. **Lint** -- Use the **lint** skill

See [Implementation Guide](references/implementation-guide.md) for detailed step-by-step.

## Debugging

- `DD_TRACE_DEBUG=true` to see patching activity and span creation
- Missing spans -- verify `_datadog_patch` guard, check `PATCH_MODULES` entry exists
- Missing event behavior -- verify the subscriber module is imported by its owning product and listens to the dispatched event name
- Wrong service name -- verify `config._add()` at module level
- **LLM-specific**: see the **llmobs-integrations** skill for failure modes and debugging

### Native Extension Build Issues

If `pip install -e .` fails with Rust compilation errors, `target3.1*` path errors, or stale cached artifacts causing builds to break, refer to
[Troubleshooting](../../../docs/troubleshooting.rst)

This clears the Rust target directory and pip's download/build caches. Run this before retrying any `pip install` that fails with native extension errors.

## Reference Files

- [Events API](../events-api/SKILL.md) -- Required guidance for typed contracts, product ownership, reuse, composition, registration, and lifecycle
- [Implementation Guide](references/implementation-guide.md) -- Step-by-step new integration (all types)
- [Reference Integrations](references/reference-integrations.md) -- All 13 categories with canonical examples
- [Contributing Integrations](../../../docs/contributing-integrations.rst) -- "What is an integration manifest?": `{name}.manifest.yaml` fields and when to update it
- [Design Decisions](references/design-decisions.md) -- Why integrations are structured this way
- [Anti-Patterns](references/anti-patterns.md) -- Silent failures and common gotchas
