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

## Typing

Fully annotate new integration functions and methods, including return types; some modules
may have mypy exemptions, so passing checks alone does not establish that new code is typed.

- Patch exports use `patch() -> None`, `unpatch() -> None`, `get_version() -> str`, and
  `_supported_versions() -> dict[str, str]`.
- A wrapt wrapper receives `(wrapped, instance, args, kwargs)`. A general signature uses
  `wrapped: Callable[..., Any]`, `instance: Any`, `args: tuple[Any, ...]`,
  `kwargs: dict[str, Any]`, and `-> Any`; use more precise types when known.
- Parameterize collections (`dict[str, Any]`, `list[str]`, `tuple[Any, ...]`), not bare
  `dict`, `list`, or `tuple`. Import needed types such as `Any` and `Callable` from `typing`.
- The package supports Python 3.10 (`pyproject.toml`). Use `Optional[T]` / `Union[...]` for
  annotations evaluated there; do not introduce `T | None` into such modules merely for style.
  Preserve an existing postponed-annotation convention where applicable.
- Avoid `# type: ignore` unless needed; use the specific error code and explain the reason.
  Validate through the `lint` skill's typing check (`scripts/lint typing`).

## Task references

- [Implementation guide](references/implementation-guide.md): new integration registration,
  manifests, test environments, and documentation.
- [Anti-patterns](references/anti-patterns.md): patch, configuration, and deferred-event pitfalls.
- [Design decisions](references/design-decisions.md): event ownership and package boundaries.
- [Contributor guide](../../../docs/contributing-integrations.rst): compatibility and manifest policy.

Use `run-tests`, `lint`, and `releasenote` for validation and PR preparation. Match current signatures
in source and add the required annotations. For native build failures, consult
[troubleshooting](../../../docs/troubleshooting.rst) before deleting caches or rebuilding.
