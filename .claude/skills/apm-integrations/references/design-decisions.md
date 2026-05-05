# Design Decisions

Why dd-trace-py integrations are structured the way they are.

## Patch Modules

Integrations use monkey-patching via `wrapt.wrap_function_wrapper` to intercept
library calls at runtime. Each integration registers in `ddtrace/_monkey.py`
(`PATCH_MODULES` dict) so `ddtrace.patch_all()` and `ddtrace-run` can auto-enable it.

The `_datadog_patch` flag on the module prevents double-patching. Always check
`getattr(mylib, '_datadog_patch', False)` before wrapping and set it to `True`.

## Pin System (DEPRECATED)

`Pin` attaches tracing metadata (service name, tracer) to library instances.
Many existing integrations use `Pin().onto(module)` and `Pin.get_from(instance)`.
Pin is DEPRECATED -- do NOT use it in new integrations. It remains in existing
integrations (redis, kafka, grpc, graphql, psycopg, flask, celery, etc.) but
new code should use `context_with_event` or `context_with_data` instead.

## The contrib/internal/ Split

Integration code lives in `ddtrace/contrib/internal/{name}/` (not `ddtrace/contrib/{name}/`).
The public API is `patch()`, `unpatch()`, and `get_version()` from `__init__.py`.
Each `__init__.py` contains RST docstrings for documentation generation --
read `ddtrace/contrib/internal/anthropic/__init__.py` for an example.

## Three Standard Patterns

**`context_with_event()` + `TracingEvent`** (NEW — preferred for new integrations):
Typed event-driven pattern using `core.context_with_event()` with `TracingEvent`
subclasses and `TracingSubscriber`. Infrastructure exists in `ddtrace/_trace/events.py`
and `ddtrace/_trace/subscribers/`. No contrib integrations use this yet, but new
ones should adopt it. See `ddtrace/internal/core/__init__.py:311` for the API.

**`context_with_data()` + `trace_handlers.py`** (preferred for existing): Wrappers use
`with core.context_with_data(...)` to emit events that `trace_handlers.py`
listeners consume (~80+ event listeners that create spans). Used by botocore,
flask, httpx, django. This pattern decouples patch code from span creation.

**Pin + tracer.trace** (DEPRECATED — do not use in new code): Patch code attaches
a `Pin` to the library module/instance, wrapper functions call
`Pin.get_from(instance)` to get the tracer, then use `tracer.trace()` to create
spans directly. Still used by redis, kafka, grpc, graphql, requests, celery, etc.

## LLM Integration Pattern

LLM integrations use `BaseLLMIntegration` (in `ddtrace/llmobs/_integrations/base.py`):
- Patch code creates a subclass instance and stores it on the module
- `integration.trace()` creates spans via `tracer.start_span()`
- Patch code directly manages span lifecycle: `span.set_exc_info()`, `span.finish()`
- `integration.llmobs_set_tags(span, ...)` handles LLMObs tag extraction
- LLMObs subclasses in `ddtrace/llmobs/_integrations/` handle message/token extraction

## Key Files

| File | Purpose |
|------|---------|
| `ddtrace/_monkey.py` | PATCH_MODULES registry |
| `ddtrace/_trace/pin.py` | Pin system (DEPRECATED for new code) |
| `ddtrace/_trace/trace_handlers.py` | Span creation for context_with_data integrations |
| `ddtrace/_trace/events.py` | TracingEvent base class (NEW events API) |
| `ddtrace/_trace/subscribers/_base.py` | TracingSubscriber base (NEW events API) |
| `ddtrace/internal/core/__init__.py` | Core API (context_with_data, context_with_event) |
| `ddtrace/internal/core/events.py` | Event + event_field() primitives |
| `ddtrace/llmobs/_integrations/base.py` | BaseLLMIntegration |
| `riotfile.py` | Test environment matrix |
