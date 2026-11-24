# Design Decisions

Why dd-trace-py integrations are structured the way they are.

## Patch Modules

Integrations use monkey-patching via `wrapt.wrap_function_wrapper` to intercept
library calls at runtime. Each integration registers in `ddtrace/_monkey.py`
(`PATCH_MODULES` dict) so `ddtrace.patch_all()` and `ddtrace-run` can auto-enable it.

The `_datadog_patch` flag on the module prevents double-patching. Always check
`getattr(mylib, '_datadog_patch', False)` before wrapping and set it to `True`.

## Integration Configuration

Read settings from `config.<integration>`. Connection-specific tags belong on the
connection or its tracing wrapper.
Direct span wrappers use `is_tracing_enabled()` to honor both the tracer setting
and standalone security mode.

## The contrib/internal/ Split

Integration code lives in `ddtrace/contrib/internal/{name}/` (not `ddtrace/contrib/{name}/`).
The public API is `patch()`, `unpatch()`, and `get_version()` from `__init__.py`.
Each `__init__.py` contains RST docstrings for documentation generation --
read `ddtrace/contrib/internal/anthropic/__init__.py` for an example.

## Three Standard Patterns

**`context_with_event()` + `TracingEvent`** (NEW — preferred for new integrations):
Typed event-driven pattern using `core.context_with_event()` with `TracingEvent`
subclasses and `TracingSubscriber`. Infrastructure exists in `ddtrace/_trace/events.py`
and `ddtrace/_trace/subscribers/`. Read concrete contrib examples such as
`ddtrace/contrib/internal/httpx/patch.py` and `ddtrace/contrib/internal/aiohttp/patch.py`.
See `ddtrace/internal/core/__init__.py` for the API. An event is available through
its synchronous `context.ended` subscriber dispatch and is released immediately
afterward. Deferred integrations must set `dispatch_end_event=False` and call
`ctx.dispatch_ended_event()` only after they have attached all final event data.

**`context_with_data()` + `trace_handlers.py`** (preferred for existing): Wrappers use
`with core.context_with_data(...)` to emit events that `trace_handlers.py`
listeners consume (~80+ event listeners that create spans). Used by botocore,
flask, httpx, django. This pattern decouples patch code from span creation.

**Direct spans** (existing integrations): Wrappers check `is_tracing_enabled()`,
read `config.<integration>`, and use `tracer.trace()` to create spans directly.
Keep this pattern when maintaining existing integrations such as Celery.

## LLM Integration Pattern

See the `llmobs-integrations` skill for LLM/AI integration architecture. This
APM guide covers the shared patch module, registration, and APM testing
workflow; LLMObs lifecycle and extraction details belong in that skill.

## Key Files

| File | Purpose |
|------|---------|
| `ddtrace/_monkey.py` | PATCH_MODULES registry |
| `ddtrace/_trace/trace_handlers.py` | Span creation for context_with_data integrations |
| `ddtrace/_trace/events.py` | TracingEvent base class (NEW events API) |
| `ddtrace/_trace/subscribers/_base.py` | TracingSubscriber base (NEW events API) |
| `ddtrace/internal/core/__init__.py` | Core API (context_with_data, context_with_event) |
| `ddtrace/internal/core/events.py` | Event + event_field() primitives |
| `tests/*/suitespec.yml` | Test environment matrix |
| `scripts/run-tests` | Test execution interface |
