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
See `ddtrace/internal/core/__init__.py` for the API. An event is available through
its synchronous `context.ended` subscriber dispatch and is released immediately
afterward. Deferred integrations must set `dispatch_end_event=False` and call
`ctx.dispatch_ended_event()` only after they have attached all final event data.
Report deferred exceptions synchronously through ContextExceptionEvent from
ddtrace.internal.core.exception rather than retaining exceptions or tracebacks on a deferred context.
The previous ddtrace.contrib._events.exception import remains a compatibility alias. Use span_bus.find_span_on_context
for optional context-local spans without creating a fallback or borrowing a parent. Completion carries final
response metadata separately. For nested middleware, each completion callback must retain
its own execution context rather than resolve a request key that inner middleware can replace.
Response preparation must reach all contexts for the request, including request clones.
Coordinate handler-task completion with final response metadata and response-sending task
completion. Outer middleware can run the handler in a child task that ends before preparation.
Dispatch completion in the captured request context so IAST state remains available. Use the
current context only when the owning request context is still active in the same task; early
preparation must clear ContextVars in the live handler. Task identity alone is insufficient
once middleware has returned. Release
completed task references immediately and remove connection-abort callbacks on every terminal
path; otherwise keep-alive connections can retain completed requests. For clones created
before instrumentation attaches request state, resolve active contexts from the app-owned
connection registry and match the shared match_info object to isolate consecutive requests.
Finish the HTTP request at preparation for a 101 protocol upgrade, independently of the
upgraded connection lifetime; use the protocol status rather than response-class checks.
Retain the returned response until deferred completion as a fallback if an earlier preparation
callback fails, while preserving metadata supplied by successful response preparation.

Products own subscriber activation. Integrations emit events without importing product
bundle declarations or registering subscriber classes. Tracing product startup and normal
global tracer initialization use the same tracing-owned helper to activate all declared
bundles. Global initialization uses an import hook to defer activation until the
subscriber package finishes loading through contrib; product startup activates immediately. Activation also occurs when
`DD_TRACE_ENABLED=false` so existing integrations can finish their spans without enabling tracing. Manual instrumentation does not need to start
the product manager.
All subscriber classes require explicit registration; class definitions only collect handlers.
The tracing subscriber package preserves activation of the migrated families for manual instrumentation,
including explicitly deferred global tracer creation. Deferred initialization activates the web request
bundle after the subscriber package finishes loading; span exception activation still requires product startup.
The generic span bundle registers `TracingSubscriber` directly. Custom subclasses must
call `register()` or be included in a bundle activated by their owning product.
Ray's SDK-dependent bundle is activated by a tracing-owned import hook after its integration patch
module finishes loading. Ordinary tracing startup does not import Ray. Repeated product startup
restores Ray listeners after that module has loaded; the integration does not activate them itself.
Do not unregister shared subscribers when unpatching one integration, since other integrations and active
requests still need their listeners.

Aiohttp keeps a task-completion fallback for deferred responses and failures that
may never prepare a response. Store the callback handle and remove it when the
request finishes: connection tasks can remain alive across keep-alive requests.
Do not retain exception objects in deferred callbacks; record errors synchronously
and allow cancellation to complete without requiring a response or tagging it as an error.

Flask and WSGI use the same web request subscriber and defer their shared request context
through response iteration. Keep application callbacks in the caller's Python Context
so outer middleware sees its bindings and Flask context tokens remain valid. Tracing
supplies response activation through the response event, saves the active span across
yields and restores the ambient span after iteration/close. Distinguish response setup
failures from streaming failures when deciding request-span error attribution.
The iterable reports completion without owning spans; the tracing-owned
`ddtrace._trace.events.WebFrameworkResponseEvent` lets tracing create the response child.
Keep new tracing lifecycle contracts outside contrib so subscriber initialization does not
add tracing-to-integration imports. Request metadata is applied before public span modifiers;
deferred WSGI completion applies response-only metadata to preserve overrides and redactions.
Keep application-return security cleanup
separate from response completion: AppSec and IAST consume
`wsgi.application.returned` while the request context remains current. Retain
Flask's metadata and blocking phase hooks rather than adding a second request owner.

Bottle maps request and response metadata into WebFrameworkRequestEvent and leaves
propagation, spans, and HTTP error policy to WebFrameworkRequestSubscriber. Raised
HTTPResponse objects set response_from_exception so the subscriber applies status-based
OpenTelemetry error semantics without deferring and re-raising the framework exception.
Set response classification before fallible header collection; header failures must not
discard a known status or alter its error classification.
Completion stays at the synchronous route callback boundary, before response iteration.

By default, WebFrameworkRequestSubscriber tags request metadata only when the request ends.
Products that read it while the handler runs, such as AI Guard's client IP and anomaly-detection
tags, then see nothing. An integration opts into request-start tagging by setting
`tag_request_on_start=True` and passing the connection's `peer_ip` (aiohttp uses
`request.remote`). The subscriber then calls `set_http_meta` with the request headers and the
peer IP when the span starts, and leaves both out of the end-of-request call. With AppSec
enabled, its own request-start call runs afterwards and replaces the request headers it records
with its cookie-free copy before the WAF runs.

**`context_with_data()` + `trace_handlers.py`** (preserve where already used): Wrappers use
`with core.context_with_data(...)` to emit events that `trace_handlers.py`
listeners consume to create spans. Used by botocore,
Flask child operations and django. This pattern decouples patch code from span creation.

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
