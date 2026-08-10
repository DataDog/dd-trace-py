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

## Test Transport Patterns

Choose the test transport that matches the closest current integration. LLMObs
tests are not all recorded the same way, and the setup details matter. Do not
replace a suite's existing VCR/proxy coverage with mocks unless the closest
reference already uses mocked SDK objects for that behavior.

### APM Test-Agent VCR Proxy

Some tests point clients at the [dd-apm-test-agent VCR feature](https://github.com/DataDog/dd-apm-test-agent#recording-3rd-party-api-requests). The APM test agent acts as a VCR proxy. Instead of calling the real provider URL, tests point the client at `http://127.0.0.1:9126/vcr/{provider}`.

The test agent:

1. **First run (recording):** Proxies the request to the real provider and records the response as a cassette file
2. **Subsequent runs (replay):** Matches incoming requests against stored cassettes by path, HTTP method, and request body, and returns the recorded response

This means tests exercise the real client library code end-to-end, including serialization, headers, and response parsing.

#### Client Setup

When following the proxy pattern, replace the provider's base URL with the test-agent VCR endpoint. Without this, the SDK will call the real provider instead of replaying cassettes.

```python
import anthropic
from google import genai
from openai import OpenAI

# Anthropic-style clients that expose base_url:
client = anthropic.Anthropic(base_url="http://127.0.0.1:9126/vcr/anthropic")

# OpenAI:
client = OpenAI(base_url="http://127.0.0.1:9126/vcr/openai")

# Google GenAI:
client = genai.Client(http_options={"base_url": "http://127.0.0.1:9126/vcr/genai"})
```

#### Test-Specific Cassette Naming

Register test context so cassette files are named per-test:

```python
import requests

requests.post("http://127.0.0.1:9126/vcr/test/start", json={"test_name": request.node.name})

# Run test...

requests.post("http://127.0.0.1:9126/vcr/test/stop")
```

#### Test Agent Configuration

| Env Variable | Default | Description |
|-------------|---------|-------------|
| `VCR_CASSETTES_DIRECTORY` | `vcr-cassettes` | Where cassette files are stored |
| `VCR_CI_MODE` | `false` | When `true`, returns 404 for missing cassettes and enforces pre-recorded cassettes |
| `VCR_PROVIDER_MAP` | built-in providers | Register custom providers: `"myprovider=http://api.example.com/"` |
| `VCR_IGNORE_HEADERS` | none | Comma-separated headers to exclude from recordings |

Built-in providers: OpenAI, Azure OpenAI, DeepSeek, Anthropic, Google GenAI, AWS Bedrock Runtime.

#### Recording New Cassettes

1. Set the real provider API key, for example `export ANTHROPIC_API_KEY=sk-ant-...`
2. Ensure `VCR_CI_MODE` is not set, or is set to `false`
3. Run the test so the test agent proxies to the real provider and records the cassette
4. Commit the cassette files
5. Enable `VCR_CI_MODE=true` in CI to prevent accidental re-recording

Full documentation: [dd-apm-test-agent VCR](https://github.com/DataDog/dd-apm-test-agent#recording-3rd-party-api-requests)

### vcrpy Cassette Fixtures

Many provider tests use local vcrpy fixtures and committed cassettes instead of
the test-agent proxy. Anthropic and Bedrock are useful references:
`tests/contrib/anthropic/utils.py` and `tests/contrib/botocore/bedrock_utils.py`
define `vcr.VCR(...)` fixtures with cassette directory, request matching, and
auth-header filtering.

Use this pattern when the SDK can call the provider normally and vcrpy can record/replay the HTTP layer. Add `vcrpy` to `riotfile.py` only for suites that need it, and follow nearby integrations for `latest` vs pinned versions.

### Mocked Provider/Client Objects

Some integrations use local mock clients or streams when external calls are not useful or stable for the behavior under test. Claude Agent SDK is a good reference: `tests/contrib/claude_agent_sdk/conftest.py` patches the internal async client and feeds mock message sequences through the real integration code.

Use mocks when testing agent loops, stream event sequences, error paths, or SDK internals that are hard to record safely. Keep mocks shaped like real SDK objects and still assert against LLMObs span data.

### API Keys

Clients often still need an API key set for client-side validation, even when replaying cassettes or using mocks. Use a placeholder:

```python
@pytest.fixture(autouse=True)
def set_api_key(monkeypatch):
    monkeypatch.setenv("ANTHROPIC_API_KEY", "test-api-key")
```

## Using `assert_llmobs_span_data()`

The canonical assertion pattern is `assert_llmobs_span_data()` from `tests/llmobs/_utils.py` with `_get_llmobs_data_metastruct(span)` from `ddtrace.llmobs._utils`. See `tests/contrib/anthropic/test_anthropic_llmobs.py` for the current reference pattern.

```python
from ddtrace.llmobs._utils import _get_llmobs_data_metastruct
from tests.llmobs._utils import assert_llmobs_span_data

def test_chat_completion(self, anthropic, anthropic_llmobs, test_spans, request_vcr):
    """Test basic chat completion produces correct LLMObs span."""
    client = anthropic.Anthropic()
    with request_vcr.use_cassette("anthropic_completion.yaml"):
        client.messages.create(
            model="claude-3-sonnet-20240229",
            messages=[{"role": "user", "content": "Hello"}],
            max_tokens=100,
        )

    spans = [s for trace in test_spans.pop_traces() for s in trace]
    assert_llmobs_span_data(
        _get_llmobs_data_metastruct(spans[0]),
        span_kind="llm",
        model_name="claude-3-sonnet-20240229",
        model_provider="anthropic",
        input_messages=[{"content": "Hello", "role": "user"}],
        output_messages=[{"content": "Hi there!", "role": "assistant"}],
        metadata={"temperature": 1.0, "max_tokens": 100},
        metrics={
            "input_tokens": 10,
            "output_tokens": 5,
            "total_tokens": 15,
        },
        tags={"ml_app": "test-app"},
    )
```

### Key Parameters

| Parameter | Type | Description |
|-----------|------|-------------|
| first positional arg | dict | LLMObs span data from `_get_llmobs_data_metastruct(span)` |
| `span_kind` | str | `"llm"`, `"agent"`, or `"tool"` |
| `name` | str | Optional LLMObs span name |
| `model_name` | str | Model identifier |
| `model_provider` | str | Provider name |
| `input_messages` | list[dict] | Input message dicts |
| `output_messages` | list[dict] | Output message dicts |
| `metadata` | dict | Request metadata |
| `metrics` | dict | Token usage metrics |
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
components:
  mylib:
    - ddtrace/contrib/internal/mylib/*

suites:
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
| `assert_llmobs_span_data(_get_llmobs_data_metastruct(span), ...)` | LLMObs span content | Flexible, focused on data | Requires VCR setup |
| Direct `assert` | Simple checks | Quick, readable | Verbose for complex objects |

**For LLMObs tests, prefer `assert_llmobs_span_data(_get_llmobs_data_metastruct(span), ...)`** -- it checks the LLMObs metastruct directly and lets you focus on the integration-specific data.

## Running Tests

Use the **run-tests** skill (`scripts/run-tests`). Never invoke `pytest`, `riot`, or `scripts/ddtest` directly.

## Test Categories

Cover these scenarios for every LLMObs integration:

1. **Basic completion** -- simple request/response, verify all LLMObs fields
2. **Streaming** -- streamed response, verify chunks are accumulated correctly
3. **Tool use** -- request with tools, verify tool calls in output and tool results in input
4. **Error handling** -- API error, verify span has error info and LLMObs still records
5. **Multi-turn** -- conversation with history, verify all messages captured
6. **Token counting** -- verify all token metrics are correct (including cache tokens)
7. **Model extraction** -- verify model name is captured from various sources
8. **Async** -- async client calls, same assertions as sync
