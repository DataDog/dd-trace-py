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

**Forgetting `span.finish()` in non-streaming paths** -- LLM integrations must
call `span.finish()` in the `finally` block for non-streaming responses.
Streaming responses finish the span when the iterator is exhausted.

**Not calling `span.set_exc_info()` on exceptions** -- Without this, error spans
won't have exception details. Always use `span.set_exc_info(*sys.exc_info())`
in except blocks.

**Missing `integration.llmobs_set_tags()` in finally block** -- Without this,
LLMObs won't capture response data. Must be called before `span.finish()`.

**Setting items on context after it exits** -- `ctx.set_item()` calls after the
`with core.context_with_data(...)` block exits are silently dropped.

## Testing

**Not adding to component AND suite in suitespec, or forgetting riotfile suite.** -- All entries required;
missing either means CI won't run tests or detect source changes.

**Using the wrong suitespec file** -- LLM/AI: `tests/llmobs/suitespec.yml`.
Standard: `tests/contrib/suitespec.yml`.

**For LLM/AI Integrations, prefer VCR cassettes over mocking/stubbing** -- Mocking / stubbing does not 
guarantee our integrations work, instead use VCR cassettes which cache HTTP request data to re run.

**VCR cassettes containing real API keys** -- Ensure `filter_headers` includes
the library's auth header name.
