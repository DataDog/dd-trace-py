# LLMObs Testing Guide

Patterns and practices for testing LLMObs integrations in dd-trace-py.

## Test File Organization

LLMObs tests live separately from APM integration tests:

| Test Type | File Pattern | Location |
|-----------|-------------|----------|
| LLMObs integration | `test_{name}_llmobs.py` | `tests/contrib/{name}/` |
| APM snapshots | `test_{name}.py` | `tests/contrib/{name}/` |
| Patch/unpatch | `test_{name}_patch.py` | `tests/contrib/{name}/` |

Example for `anthropic`:
```
tests/contrib/anthropic/
├── test_anthropic.py           # APM snapshot tests
├── test_anthropic_llmobs.py    # LLMObs integration tests
├── test_anthropic_patch.py     # Patch/unpatch tests
```

## VCR Cassettes via APM Test Agent

**Do not mock response objects.** LLMObs tests use the [dd-apm-test-agent VCR feature](https://github.com/DataDog/dd-apm-test-agent#recording-3rd-party-api-requests) to record and replay real provider API responses.

### How It Works

The APM test agent acts as a VCR proxy. Instead of calling the real provider URL, tests point the client at `http://127.0.0.1:9126/vcr/{provider}`. The test agent:

1. **First run (recording):** Proxies the request to the real provider, records the response as a cassette file
2. **Subsequent runs (replay):** Matches incoming requests against stored cassettes by path, HTTP method, and request body, and returns the recorded response

This means tests exercise the **real client library code** end-to-end, including serialization, headers, and response parsing -- no mocks needed.

### Client Setup

Replace the provider's base URL with the test agent VCR endpoint:

```python
import anthropic

# Point at APM test agent VCR proxy instead of real Anthropic API
client = anthropic.Anthropic(base_url="http://127.0.0.1:9126/vcr/anthropic")

# For OpenAI:
from openai import OpenAI
client = OpenAI(base_url="http://127.0.0.1:9126/vcr/openai")
```

### Test-Specific Cassette Naming

Register test context so cassette files are named per-test:

```python
import requests

# Before test
requests.post("http://127.0.0.1:9126/vcr/test/start", json={"test_name": request.node.name})

# Run test...

# After test
requests.post("http://127.0.0.1:9126/vcr/test/stop")
```

### Test Agent Configuration

| Env Variable | Default | Description |
|-------------|---------|-------------|
| `VCR_CASSETTES_DIRECTORY` | `vcr-cassettes` | Where cassette files are stored |
| `VCR_CI_MODE` | `false` | When `true`, returns 404 for missing cassettes (enforces all cassettes pre-recorded) |
| `VCR_PROVIDER_MAP` | built-in providers | Register custom providers: `"myprovider=http://api.example.com/"` |
| `VCR_IGNORE_HEADERS` | none | Comma-separated headers to exclude from recordings |

Built-in providers: OpenAI, Azure OpenAI, DeepSeek, Anthropic, Google GenAI, AWS Bedrock Runtime.

### Recording New Cassettes

1. Set real API key: `export ANTHROPIC_API_KEY=sk-ant-...`
2. Ensure `VCR_CI_MODE` is not set (or `false`)
3. Run the test -- the test agent proxies to the real provider and records the cassette
4. Commit the cassette files
5. Enable `VCR_CI_MODE=true` in CI to prevent accidental re-recording

### API Keys

Clients still need an API key set (for client-side validation), but it doesn't need to be valid when replaying from cassettes. Use a placeholder:

```python
@pytest.fixture(autouse=True)
def set_api_key(monkeypatch):
    monkeypatch.setenv("ANTHROPIC_API_KEY", "test-api-key")
```

Full documentation: [dd-apm-test-agent VCR](https://github.com/DataDog/dd-apm-test-agent#recording-3rd-party-api-requests)

## Using `_expected_llmobs_llm_span_event()`

The canonical assertion helper is `_expected_llmobs_llm_span_event` from `tests/llmobs/_utils.py`. **Always use this helper -- never write manual field-by-field checks.**

```python
from tests.llmobs._utils import _expected_llmobs_llm_span_event

def test_chat_completion(self, llmobs):
    """Test basic chat completion produces correct LLMObs span."""
    client = anthropic.Anthropic(base_url="http://127.0.0.1:9126/vcr/anthropic")
    resp = client.messages.create(
        model="claude-3-sonnet-20240229",
        messages=[{"role": "user", "content": "Hello"}],
        max_tokens=100,
    )

    expected = _expected_llmobs_llm_span_event(
        span,
        span_kind="llm",
        model_name="claude-3-sonnet-20240229",
        model_provider="anthropic",
        input_messages=[{"content": "Hello", "role": "user"}],
        output_messages=[{"content": "Hi there!", "role": "assistant"}],
        metadata={"temperature": 1.0, "max_tokens": 100},
        token_metrics={
            "input_tokens": 10,
            "output_tokens": 5,
            "total_tokens": 15,
        },
        tags={"ml_app": "test-app"},
    )
    llmobs_writer = llmobs._instance._llmobs_writer
    llmobs_writer.enqueue.assert_called_with(expected)
```

### Key Parameters

| Parameter | Type | Description |
|-----------|------|-------------|
| `span` | Span | The traced span object |
| `span_kind` | str | `"llm"`, `"agent"`, or `"tool"` |
| `model_name` | str | Model identifier |
| `model_provider` | str | Provider name |
| `input_messages` | list[dict] | Input message dicts |
| `output_messages` | list[dict] | Output message dicts |
| `metadata` | dict | Request metadata |
| `token_metrics` | dict | Token usage metrics |
| `tags` | dict | Additional tags |
| `tool_definitions` | list[dict] | Tool definition dicts (optional) |

### Message Format in Assertions

Messages in test assertions use plain dicts, not `Message` objects:

```python
# Input with tool result
input_messages=[
    {"content": "Hello", "role": "user"},
    {
        "content": "",
        "role": "user",
        "tool_results": [{"result": "72F", "tool_id": "toolu_123", "type": "tool_result"}],
    },
]

# Output with tool call
output_messages=[
    {
        "content": "",
        "role": "assistant",
        "tool_calls": [
            {
                "name": "get_weather",
                "arguments": {"city": "NYC"},
                "tool_id": "toolu_456",
                "type": "tool",
            }
        ],
    },
]
```

## Suitespec Configuration

LLMObs tests go in `tests/llmobs/suitespec.yml`, NOT in `tests/contrib/suitespec.yml`:

```yaml
# tests/llmobs/suitespec.yml
mylib:
  parallelism: 1
  paths:
    - tests/contrib/mylib/test_mylib_llmobs.py
  env:
    MYLIB_API_KEY: "test-key"
  dependencies:
    - mylib>=1.0
```

## Snapshot vs Manual Assertions

| Approach | When to Use | Pros | Cons |
|----------|------------|------|------|
| Snapshot (`.json` files) | APM span structure | Exact match, easy to update | Brittle to formatting changes |
| `_expected_llmobs_llm_span_event()` | LLMObs span content | Flexible, focused on data | Requires VCR setup |
| Direct `assert` | Simple checks | Quick, readable | Verbose for complex objects |

**For LLMObs tests, always prefer `_expected_llmobs_llm_span_event()`** -- it handles the boilerplate span event structure and lets you focus on the integration-specific data.

## Running Tests

Use the **run-tests** skill (`scripts/run-tests`). Never invoke `pytest`, `riot`, or `scripts/ddtest` directly.

## Test Categories

Cover these scenarios for every LLMObs integration:

1. **Basic completion** -- simple request/response, verify all context items
2. **Streaming** -- streamed response, verify chunks are accumulated correctly
3. **Tool use** -- request with tools, verify tool calls in output and tool results in input
4. **Error handling** -- API error, verify span has error info and LLMObs still records
5. **Multi-turn** -- conversation with history, verify all messages captured
6. **Token counting** -- verify all token metrics are correct (including cache tokens)
7. **Model extraction** -- verify model name is captured from various sources
8. **Async** -- async client calls, same assertions as sync
