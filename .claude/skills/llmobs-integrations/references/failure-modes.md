# LLMObs Failure Modes

Comprehensive debugging guide for all known LLMObs integration failure modes. Each entry includes symptoms, causes, and fixes.

---

## `llmobs_not_enabled` -- LLMObs Not Recording

**Symptoms:**
- No LLMObs spans appear in Datadog
- APM spans exist but have no LLMObs fields
- `llmobs_writer.enqueue()` never called

**Causes:**
1. `submit_to_llmobs=True` not set on `LlmRequestEvent` for event-based patch code
2. `ctx.dispatch_ended_event()` not called, so `LlmTracingSubscriber` never calls `integration.llmobs_set_tags()`
3. Integration not instantiated -- `module._datadog_integration` is `None`
4. `llmobs_enabled` returns `False` -- LLMObs not configured in tracer config

**Fix:**
- Verify `LlmRequestEvent(..., submit_to_llmobs=True, llmobs_integration=integration, request_kwargs=kwargs, ...)` in event-based patch wrappers
- Verify success and error paths call `ctx.dispatch_ended_event(...)`
- Verify `patch()` stores integration: `module._datadog_integration = MyIntegration(integration_config=config.mylib)`
- Check `DD_LLMOBS_ENABLED=1` is set

---

## `wrong_messages` -- Input/Output Messages Incorrect

**Symptoms:**
- LLMObs spans exist but `input_messages` or `output_messages` are empty, truncated, or malformed
- Tool calls appear as raw text instead of structured `ToolCall` objects
- Multi-part content shows only the first block

**Causes:**
1. Library's message format differs from expected -- content is a list of blocks, not a string
2. Multi-part content not iterated (only first block extracted)
3. Tool use blocks not parsed into `ToolCall` objects
4. Tool result blocks not parsed into `ToolResult` objects
5. System message handled differently (separate kwarg vs first message)

**Fix:**
- Read the library's API response objects to understand the actual structure
- Handle both string and list content: `isinstance(content, list)` check
- Parse all content block types: `text`, `image`, `tool_use`, `tool_result`
- Use `Message(content=..., role=..., tool_calls=[...])` for outputs with tool calls
- Use `Message(content=..., role=..., tool_results=[...])` for inputs with tool results
- Check system message handling (anthropic uses separate `system` kwarg)

---

## `wrong_tokens` -- Token Metrics Incorrect

**Symptoms:**
- Token counts are 0 or missing in LLMObs spans
- Input/output token split is wrong
- Total tokens doesn't match sum of input + output

**Causes:**
1. Library uses different field names (e.g., `prompt_tokens` vs `input_tokens`)
2. Usage object is nested differently (attribute vs dict)
3. Cache tokens not included in total (anthropic has `cache_creation_input_tokens`, `cache_read_input_tokens`)
4. Streaming responses don't include usage in every chunk (only final chunk)

**Fix:**
- Check library docs/source for exact usage field names
- Use `getattr(usage, "field_name", 0)` for attribute access
- Normalize input tokens to the total input tokens sent to the model. For Anthropic, use `input_tokens + cache_creation_input_tokens + cache_read_input_tokens`; for OpenAI, prompt/input tokens are already the combined input total and cached tokens appear separately in details.
- For streaming: capture usage from the final event/chunk type
- Map to standard keys: `INPUT_TOKENS_METRIC_KEY`, `OUTPUT_TOKENS_METRIC_KEY`, `TOTAL_TOKENS_METRIC_KEY`

---

## `wrong_model` -- Model Name Not Captured

**Symptoms:**
- LLMObs spans show empty or `"unknown"` model name
- Model appears in APM tags but not in LLMObs context

**Causes:**
1. Model name is in a different kwarg (e.g., `model_id` vs `model`)
2. Model name is on the response object, not in kwargs
3. Model name is on the client instance, not per-request
4. Model name includes version suffix that needs normalization

**Fix:**
- Check kwargs first: `kwargs.get("model", "")`
- Fall back to response: `getattr(response, "model", "")`
- Fall back to instance: `getattr(instance, "_model", "")`
- Set in both `_set_base_span_tags()` (APM) and `_llmobs_set_tags()` (LLMObs)

---

## `streaming_not_handled` -- Streaming Responses Broken

**Symptoms:**
- Streaming requests produce no LLMObs data
- Span finishes before stream is consumed
- Stream appears to work but output messages are empty
- Application hangs or stream is consumed twice

**Causes:**
1. Not using `BaseStreamHandler` subclass -- raw iteration loses trace context
2. Stream consumed in wrapper before returning to caller
3. `span.finish()` called before stream is exhausted
4. `finalize_stream()` doesn't complete the span lifecycle: event-based integrations must dispatch the ended event; direct-trace integrations must call `integration.llmobs_set_tags()` and `span.finish()`
5. Token usage not captured from final stream event

**Fix:**
- Subclass `StreamHandler` (sync) or `AsyncStreamHandler` (async)
- Implement `process_chunk()` to accumulate data without consuming the stream
- Implement `finalize_stream()` to build the response object and complete the right lifecycle. For `LlmRequestEvent` integrations, set `ctx.event.response` and call `ctx.dispatch_ended_event(...)`; for direct-trace integrations, call `llmobs_set_tags()` and `span.finish()`.
- Use `make_traced_stream(response, handler)` to wrap the response
- In patch code: return the traced stream instead of the raw response
- Capture usage from final chunk type (varies by library)

---

## `async_not_working` -- Async Calls Not Traced

**Symptoms:**
- Async API calls produce no spans
- Sync calls work fine, async calls are invisible
- `RuntimeWarning: coroutine was never awaited`

**Causes:**
1. Using sync wrapper function for async method
2. Not awaiting `wrapped(*args, **kwargs)` in async wrapper
3. Missing async stream handler (using sync `StreamHandler` for async stream)
4. Patch didn't wrap the async client methods

**Fix:**
- Create separate `traced_async_*` wrapper functions with `async def`
- Use `await wrapped(*args, **kwargs)` in async wrappers
- Subclass `AsyncStreamHandler` for async streams
- Verify `patch()` wraps both sync and async client methods:
  - `wrapt.wrap_function_wrapper("lib", "Client.create", traced_create)`
  - `wrapt.wrap_function_wrapper("lib", "AsyncClient.create", traced_async_create)`

---

## `tool_calls_missing` -- Tool Use Not Captured

**Symptoms:**
- LLMObs spans exist but tool calls/results are missing
- Tool definitions not shown in span metadata
- Agent workflows appear as simple LLM calls

**Causes:**
1. Tool use blocks in response not parsed into `ToolCall` objects
2. Tool result blocks in input not parsed into `ToolResult` objects
3. Tool definitions from request `tools` kwarg not extracted
4. Content block iteration skips non-text types

**Fix:**
- In `_extract_output_messages()`: parse `tool_use` blocks into `ToolCall(name, arguments, tool_id, type="tool")`
- In `_extract_input_messages()`: parse `tool_result` blocks into `ToolResult(result, tool_id, type="tool_result")`
- In `_extract_tools()`: convert request `tools` kwarg to `ToolDefinition` list
- Set `TOOL_DEFINITIONS` context item when tools are present
- For agent patterns: create child tool spans with `SPAN_KIND: "tool"`

---

## `test_transport_not_working` -- Cassettes, Proxy, or Mocks Not Replaying

LLMObs integration tests use multiple transport patterns: local vcrpy fixtures, the dd-apm-test-agent VCR proxy, and local mocked clients/streams. Follow the closest current integration.

**Symptoms:**
- Tests fail with network errors when cassettes should replay
- Cassette files are empty or not created
- Tests pass locally but fail in CI
- `ConnectionError` or `APIError` during test execution
- Mocked stream/client tests produce spans with empty messages or token metrics

**Causes:**
1. vcrpy fixture does not match the real request path/body/headers
2. Client base URL not pointed at test agent VCR endpoint when using the proxy pattern (`http://127.0.0.1:9126/vcr/{provider}`)
3. Cassette recorded with different library version -- request format changed, matching fails
4. `VCR_CI_MODE=true` in CI but cassette doesn't exist (returns 404)
5. API key env var not set -- client validates key before sending even when VCR replays
6. Custom provider not registered in `VCR_PROVIDER_MAP` for proxy tests
7. Mock object shape no longer matches the SDK's real response/stream events

**Fix:**
- For vcrpy fixture tests, verify cassette directory, `match_on`, and auth-header filtering match the reference integration
- For test-agent proxy tests, verify client uses `base_url="http://127.0.0.1:9126/vcr/{provider}"` and the test agent is reachable
- Re-record cassettes when upgrading library version or changing request shape
- Set placeholder API key env vars: `ANTHROPIC_API_KEY=test-key`
- For custom providers, set `VCR_PROVIDER_MAP="myprovider=http://api.example.com/"`
- For mocked client/stream tests, update mocks from the latest SDK object shape and keep assertions focused on LLMObs span data
- Ensure cassette files are committed to the repository when the chosen pattern uses cassettes
