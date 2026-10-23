---
name: apm-integrations
description: Create or modify contrib instrumentation, integration registration, manifests, and APM integration tests.
---

# APM integrations

Use for changes to `ddtrace/contrib/internal/` or integration registration. Read the affected
integration and its tests first; consult another [reference integration](references/reference-integrations.md)
when introducing a pattern or resolving a design question.

## Choose the existing lifecycle

- New standard integrations: prefer typed `TracingEvent` + `core.context_with_event()`.
  References: `ddtrace/contrib/internal/httpx/patch.py`, `ddtrace/contrib/internal/aiohttp/patch.py`,
  and `ddtrace/_trace/subscribers/`.
- Existing `context_with_data()` integrations: preserve their event/handler contract when extending
  them. See `ddtrace/_trace/trace_handlers.py`; do not mix lifecycle owners accidentally.
- Pin remains in existing integrations, but do not introduce it into new integrations.
- LLM/AI lifecycle and extraction: use `llmobs-integrations` when those behaviors change.

Preserve patch idempotency and symmetric unpatching, including deferred ModuleWatchdog hooks.
Keep optional security-product logic out of contrib; follow `.cursor/rules/isolated-responsibility.mdc`.

## Task references

- [Implementation guide](references/implementation-guide.md): new integration registration,
  manifests, test environments, and documentation.
- [Anti-patterns](references/anti-patterns.md): patch, configuration, and deferred-event pitfalls.
- [Design decisions](references/design-decisions.md): event ownership and package boundaries.
- [Contributor guide](../../../docs/contributing-integrations.rst): compatibility and manifest policy.

Use `run-tests`, `lint`, and `releasenote` for validation and PR preparation. Match current signatures
and typing conventions in source. For native build failures, consult
[troubleshooting](../../../docs/troubleshooting.rst) before deleting caches or rebuilding.
