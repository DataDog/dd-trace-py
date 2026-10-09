# Anti-Patterns & Silent Failures

Common mistakes that don't produce errors but break tracing functionality.

## Patching

**Forgetting `_datadog_patch` guard** -- `patch()` wraps methods multiple times
on repeated calls, creating duplicate spans. Always check
`getattr(module, '_datadog_patch', False)` before wrapping.

**Wrong module path in `wrap_function_wrapper`** -- Wrapping silently does nothing
if the import path doesn't match the actual module structure.

**Not calling `unpatch()` symmetrically** -- Every `wrap_function_wrapper` in
`patch()` needs a corresponding `unwrap()` in `unpatch()`. Missing unwraps
produce orphaned spans after `unpatch()`.

**Injecting arguments by assigning `kwargs[name]`** -- Breaks callers (and library
subclasses) that pass the argument positionally (`falcon.asgi.App` forwards
`middleware` positionally, so `kwargs["middleware"] = ...` raised "got multiple
values"), crashes on legal values such as `None` or a single object, and mutates
caller-owned containers. Read and replace the argument with
`get_argument_value`/`set_argument_value`, normalize it the way the library does,
and build a new container.

**Wrapping a base-class method and a subclass method that calls `super()`** -- The
wrapper runs twice per call (e.g. `falcon.API.__init__` → `falcon.App.__init__`),
duplicating spans or injected middleware. Wrap only the base method.

**Patching only already-imported lazy modules** -- Deferred/lazy-loaded classes
may not exist at `patch()` time. Register a `ModuleWatchdog` module hook so the
wrapper is installed after the target module imports. Make hook registration
idempotent, make the hook avoid wrapping a target twice, and have `unpatch()`
both unregister every hook and unwrap every target the hook already patched.
Keep the exact module-name/hook pairs so registration and cleanup are
symmetric.

## Configuration

**Forgetting `config._add()` at module level** -- Config must be registered
before `patch()` runs, not inside `patch()`.

**Using Pin in new integrations** -- Pin is DEPRECATED. Do NOT use
`Pin().onto()` / `Pin.get_from()` in new integrations. Use `context_with_event`
(preferred for new code) or `context_with_data` instead. Pin remains in many
existing integrations but should not be added to new ones.

**Using `context_with_data` when `context_with_event` is available** -- For new
integrations, prefer the typed `context_with_event()` + `TracingEvent` pattern
over `context_with_data()`. The events API provides better type safety and
decoupling. Infrastructure: `ddtrace/_trace/events.py`, `ddtrace/_trace/subscribers/`.

## Span Lifecycle

**Not calling `span.set_exc_info()` on exceptions** -- Without this, error spans
won't have exception details. Always use `span.set_exc_info(*sys.exc_info())`
in direct span-management except blocks.

**Finishing LLM spans only from a generator finally** -- Client disconnect or
an abandoned iterator can skip that finally, leaving the span in the aggregator
so later requests nest under it. ASGI request teardown finishes leftover LLM
descendants after the app callable returns (not when the last body chunk is
sent). `TracedStream.__del__` finalizes dropped `next()` iteration.
`__exit__`/`__aexit__` finalize unexhausted context managers, including when
wrapped async cleanup raises `CancelledError`. Still annotate on the happy
path from generator finally.

**Setting items on context after it exits** -- `ctx.set_item()` calls after the
`with core.context_with_data(...)` block exits are silently dropped.

For LLM/AI integrations, see the `llmobs-integrations` skill for event-based
span lifecycle anti-patterns (`ctx.dispatch_ended_event`, streaming, and
direct-trace exceptions).

## Testing

**Not adding to both component AND suite in suitespec** -- Both entries required;
missing either means CI won't run tests or detect source changes.

**Testing only manual instrumentation** -- If every test installs the integration's
middleware or wrapper by hand, the `patch()` path that `ddtrace-run` users get is
never exercised. Test the autopatched constructors and entry points too, including
library subclasses that inherit the patched method (e.g. ASGI variants). When
patching can't be undone in-process, use `@pytest.mark.subprocess(ddtrace_run=True)`.

**Using the wrong suitespec file** -- LLM/AI: `tests/llmobs/suitespec.yml`.
Standard: `tests/contrib/suitespec.yml`.

**VCR cassettes containing real API keys** -- Ensure `filter_headers` includes
the library's auth header name.
