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
integrations (redis, kafka, grpc, psycopg, flask, celery, etc.) but
new code should use `context_with_event` or `context_with_data` instead.

## The contrib/internal/ Split

Integration code lives in `ddtrace/contrib/internal/{name}/` (not `ddtrace/contrib/{name}/`).
Patch exports live in `patch.py`; users normally enable instrumentation via `ddtrace.patch()`.
Each integration `__init__.py` contains RST docstrings for documentation generation --
read `ddtrace/contrib/internal/anthropic/__init__.py` for an example.

## Three Standard Patterns

**`context_with_event()` + `TracingEvent`** (preferred for new integrations):
Typed event-driven pattern using `core.context_with_event()` with `TracingEvent`
subclasses and `TracingSubscriber`. Infrastructure exists in `ddtrace/_trace/events.py`
and `ddtrace/_trace/subscribers/`. Read concrete contrib examples such as
`ddtrace/contrib/internal/httpx/patch.py` and `ddtrace/contrib/internal/aiohttp/patch.py`.
Subscriber definitions never activate listeners. Owning products must call `register()` explicitly, directly or through a product-owned
`SubscriberBundle`. Shared tracing bundles live in `ddtrace/_trace/_registration.py`;
normal global tracer initialization and tracing product startup activate them.
Integrations must not import tracing bundles or activate tracing subscribers from
patch functions or manual middleware setup.
A bundle names one module and one or more classes; compose bundles for multiple modules.
Retain completion listeners while active requests can still finish.
See `ddtrace/internal/core/__init__.py` for the API. An event is available through
its synchronous `context.ended` subscriber dispatch and is released immediately
afterward. Deferred integrations must set `dispatch_end_event=False` and call
`ctx.dispatch_ended_event()` only after they have attached all final event data.
Lifecycle dispatch must not depend on a tracing span being present: with tracing
disabled, other products can still consume the same events. Do not call
`span_from_context()` simply to check whether a request can be finalized, since it
creates a fallback span when no tracing subscriber is registered.
Emit events without computing tracing product enablement. Web request events start
with span_creation_enabled false; the tracing-owned admission policy sets it only
when a request receives an owned span. Standalone span requirements are handled
separately from APM admission. An explicit WEB_FRAMEWORK_SUBSCRIBERS.unregister()
closes APM admission and drains active requests; tracing startup uses
ensure_registered() rather than explicitly reopening the bundle. Integration setup
does not activate product listeners.
Only an explicit owner register() call reopens manual admission.

All shared web integrations must tolerate a missing request span: update metadata
and dispatch completion regardless, and avoid span_from_context() fallback lookup.
Do not infer ownership from _inner_span alone, because AppSec can borrow a parent.
Keep integration enablement separate from product listener ownership. Core resets
invalidate cached bundle activation, but do not override tracing's manual admission policy. Retain completion listeners until
requests admitted by that owner finish; unpatching one integration must not remove
shared listeners needed by other integrations.

**`context_with_data()` + `trace_handlers.py`** (preserve where already used): Wrappers use
`with core.context_with_data(...)` to emit events that `trace_handlers.py`
listeners consume to create spans. Used by botocore,
flask and django. This pattern decouples patch code from span creation.

**Pin + tracer.trace** (DEPRECATED — do not use in new code): Patch code attaches
a `Pin` to the library module/instance, wrapper functions call
`Pin.get_from(instance)` to get the tracer, then use `tracer.trace()` to create
spans directly. Still used by redis, kafka, grpc, celery, etc.

## LLM Integration Pattern

See the `llmobs-integrations` skill for LLM/AI integration architecture. This
APM guide covers the shared patch module, registration, and APM testing
workflow; LLMObs lifecycle and extraction details belong in that skill.

## Key Files

| File | Purpose |
|------|---------|
| `ddtrace/_monkey.py` | PATCH_MODULES registry |
| `ddtrace/_trace/pin.py` | Pin system (DEPRECATED for new code) |
| `ddtrace/_trace/trace_handlers.py` | Span creation for context_with_data integrations |
| `ddtrace/_trace/events.py` | TracingEvent base class |
| `ddtrace/_trace/subscribers/_base.py` | TracingSubscriber base |
| `ddtrace/internal/core/__init__.py` | Core API (context_with_data, context_with_event) |
| `ddtrace/internal/core/events.py` | Event + event_field() primitives |
| `tests/*/suitespec.yml` | Test environment matrix |
| `scripts/run-tests` | Test execution interface |
