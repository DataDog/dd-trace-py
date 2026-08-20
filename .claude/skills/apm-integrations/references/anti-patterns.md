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

**Patching before import completes** -- Deferred/lazy-loaded classes may not
exist at `patch()` time. The wrap succeeds but wraps a stale reference.

**Guarding a lazy submodule with an attribute walk** -- Packages that load
submodules lazily (PEP 562 `__getattr__`) report a real module as missing until
something imports it. Walking the dotted path off the root package, as
`check_module_path(pkg, "a.b.c.func")` does, then returns `False` and `patch()`
skips a wrap that would have worked -- no error, no spans. Import the module
explicitly instead, and check the symbol on it:

```python
try:
    mod = importlib.import_module("pkg.a.b.c")
except ImportError:
    mod = None
if mod is not None and hasattr(mod, "func"):
    wrap("pkg", "a.b.c.func", _traced_func)
```

The explicit import also binds the intermediate attributes, so wrapt can resolve
the dotted path. See `ddtrace/contrib/internal/google_adk/patch.py`.

**Guarding `unpatch()` on the symbol instead of the wrapper** -- `unwrap()`
raises when an attribute was never wrapped. A symbol that `patch()` skipped can
be importable by the time `unpatch()` runs, so guard on `iswrapped(mod, name)`,
not on whether the symbol exists.

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

**Setting items on context after it exits** -- `ctx.set_item()` calls after the
`with core.context_with_data(...)` block exits are silently dropped.

For LLM/AI integrations, see the `llmobs-integrations` skill for event-based
span lifecycle anti-patterns (`ctx.dispatch_ended_event`, streaming, and
direct-trace exceptions).

## Testing

**Not adding to both component AND suite in suitespec** -- Both entries required;
missing either means CI won't run tests or detect source changes.

**Using the wrong suitespec file** -- LLM/AI: `tests/llmobs/suitespec.yml`.
Standard: `tests/contrib/suitespec.yml`.

**VCR cassettes containing real API keys** -- Ensure `filter_headers` includes
the library's auth header name.
