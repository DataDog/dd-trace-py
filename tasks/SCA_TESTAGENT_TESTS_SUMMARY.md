# SCA FastAPI TestAgent Integration Tests - Implementation Summary

## Overview

Created comprehensive end-to-end integration tests for SCA (Software Composition Analysis) runtime instrumentation using the FastAPI test application and test agent infrastructure. **These tests correctly use the test agent API to simulate Remote Configuration updates** to the running uvicorn process.

## Files Created/Modified

### 1. **tests/appsec/integrations/fastapi_tests/test_sca_fastapi_testagent.py** (NEW)

Comprehensive test suite for SCA detection with 11 test cases, following the same pattern as `test_flask_remoteconfig.py`.

#### Key Infrastructure Functions

**`_sca_rc_config(token, targets)`**
- Sends SCA detection configuration via Remote Config through the test agent
- Uses the test agent endpoint: `/test/session/responses/config/path`
- Simulates the RC backend sending instrumentation targets to the running application
- Path format: `"datadog/2/SCA_DETECTION/sca_config/config"`
- Payload format: `{"targets": ["os.path:join", ...]}`

**`_sca_rc_config_full(token, targets)`**
- Sends complete RC payload with proper structure (signatures, targets, hashes)
- Uses the test agent endpoint: `/test/session/responses/config`
- Includes base64-encoded payloads and SHA256 hashes
- Follows the full Remote Configuration protocol structure

**`_wait_for_sca_instrumentation(client, endpoint, max_retries, sleep_time)`**
- Waits for RC updates to be processed and instrumentation to be applied
- Makes requests to the endpoint and waits for 200 responses
- Accounts for async RC processing in the separate uvicorn process

#### Test Coverage

1. **test_sca_basic_instrumentation**
   - Validates basic SCA instrumentation via RC
   - Sends RC config to instrument `os.path:join`
   - Verifies span tags: `_dd.sca.instrumented`, `_dd.sca.detection_hit`, `_dd.sca.target`
   - Confirms instrumentation works across process boundary

2. **test_sca_multiple_calls** (parametrized: multiprocess True/False)
   - Tests tracking multiple invocations
   - Instruments multiple functions via single RC payload
   - Validates hit counting

3. **test_sca_async_function**
   - Confirms SCA works with `async def` endpoints
   - Validates bytecode instrumentation for coroutines

4. **test_sca_form_data**
   - Tests SCA with POST requests and form data
   - Validates instrumentation persists across different HTTP methods

5. **test_sca_nested_calls**
   - Tests SCA detection in nested function call patterns
   - Instruments `os.path:join` and `os.path:dirname`

6. **test_sca_no_instrumentation_without_config**
   - **Negative test**: Validates no SCA tags without RC configuration
   - Ensures instrumentation is truly dynamic (not static)

7. **test_sca_concurrent_requests**
   - Tests SCA with 5 concurrent requests (ThreadPoolExecutor)
   - Validates thread-safe hit recording in the uvicorn process

8. **test_sca_span_tags_format**
   - Validates span tag format and structure
   - Ensures tags contain qualified names with `:` separator
   - Checks all required tags are present

9. **test_sca_full_rc_payload**
   - Tests with complete RC payload structure (not simplified path endpoint)
   - Validates compatibility with full RC protocol

10. **test_sca_multiple_endpoints** (parametrized: 3 endpoints)
    - Tests SCA across different endpoint types
    - Validates consistency across application

### 2. **tests/appsec/integrations/fastapi_tests/app.py** (MODIFIED)

Added 5 new SCA-specific endpoints for testing (lines 283-358):

1. **GET /sca-vulnerable-function**
   - Basic endpoint calling `os.path.join`
   - Used for simple instrumentation tests

2. **POST /sca-vulnerable-request**
   - Accepts form data
   - Calls instrumented function with user input

3. **GET /sca-multiple-calls**
   - Makes 3 calls to `os.path.join` in a loop
   - Also calls `os.path.exists`
   - Tests hit counting

4. **GET /sca-async-vulnerable**
   - Async endpoint with `await` operations
   - Calls instrumented function within async context

5. **GET /sca-nested-calls**
   - Nested function calls with instrumentation
   - Helper function calls `os.path.dirname`
   - Main function calls `os.path.join`

## Correct Test Pattern: Cross-Process Remote Configuration

### The Challenge

The uvicorn server runs in a **separate process** (subprocess), so:
- ❌ **Cannot** mock callbacks in the test process
- ❌ **Cannot** directly call SCA functions from the test
- ✅ **Must** use the test agent API to inject RC payloads

### The Solution

Use test agent HTTP endpoints to simulate Remote Configuration:

```python
def _sca_rc_config(token, targets):
    """Send RC config via test agent."""
    path = "datadog/2/SCA_DETECTION/sca_config/config"
    msg = {"targets": targets}

    client = _get_agent_client()
    client.request(
        "POST",
        "/test/session/responses/config/path?test_session_token=%s" % (token,),
        json.dumps({"path": path, "msg": msg}),
    )
    resp = client.getresponse()
    assert resp.status == 202
```

### Test Flow

```
┌──────────────┐                 ┌──────────────┐                 ┌──────────────┐
│ Test Process │                 │  Test Agent  │                 │    Uvicorn   │
│              │                 │   (HTTP)     │                 │   Process    │
└──────────────┘                 └──────────────┘                 └──────────────┘
       │                                │                                │
       │  1. Start uvicorn with token  │                                │
       │───────────────────────────────────────────────────────────────>│
       │                                │                                │
       │  2. POST /test/session/...    │                                │
       │      with RC payload           │                                │
       │──────────────────────────────>│                                │
       │                                │                                │
       │                                │  3. RC Poller requests config  │
       │                                │<───────────────────────────────│
       │                                │                                │
       │                                │  4. Return RC payload          │
       │                                │───────────────────────────────>│
       │                                │                                │
       │                                │         5. Apply instrumentation
       │                                │            (in uvicorn process)
       │                                │                                │
       │  6. Make HTTP request to app  │                                │
       │───────────────────────────────────────────────────────────────>│
       │                                │                                │
       │  7. Response (instrumented)   │                                │
       │<───────────────────────────────────────────────────────────────│
       │                                │                                │
       │  8. GET /test/session/traces  │                                │
       │──────────────────────────────>│                                │
       │                                │                                │
       │  9. Return spans with SCA tags│                                │
       │<──────────────────────────────│                                │
```

## Environment Configuration

All tests use standardized environment variables:

```python
SCA_ENV = {
    "DD_APPSEC_SCA_ENABLED": "true",       # Main SCA product opt-in
    "DD_SCA_DETECTION_ENABLED": "true",     # Runtime detection feature
    "DD_APPSEC_ENABLED": "true",            # AppSec must be enabled
    "DD_REMOTE_CONFIGURATION_ENABLED": "true",  # RC must be enabled
}
```

Plus the uvicorn server automatically sets:
- `DD_REMOTE_CONFIG_POLL_INTERVAL_SECONDS=0.5` (fast polling for tests)
- Test session token headers for trace correlation

## RC Payload Format

### Simplified (via `/config/path` endpoint):

```json
{
  "path": "datadog/2/SCA_DETECTION/sca_config/config",
  "msg": {
    "targets": [
      "os.path:join",
      "os.path:exists"
    ]
  }
}
```

### Full RC Protocol (via `/config` endpoint):

```json
{
  "roots": ["<base64-encoded-root-metadata>"],
  "targets": "<base64-encoded-targets-data>",
  "target_files": [
    {
      "path": "datadog/2/SCA_DETECTION/sca_config/config",
      "raw": "<base64-encoded-payload>"
    }
  ],
  "client_configs": ["datadog/2/SCA_DETECTION/sca_config/config"]
}
```

## Validation Strategy

### Span Tag Verification

Tests validate three critical span tags:

1. **`_dd.sca.instrumented`**: `"true"` - Span has SCA instrumentation
2. **`_dd.sca.detection_hit`**: `"true"` - Instrumented function was called
3. **`_dd.sca.target`**: `"os.path:join"` - Which function was hit

### Test Pattern

```python
# 1. Start server with SCA enabled
with uvicorn_server(appsec_enabled="true", token=token, env=SCA_ENV):
    # 2. Send RC config via test agent
    _sca_rc_config(token, ["os.path:join"])

    # 3. Wait for instrumentation to be applied
    _wait_for_sca_instrumentation(client, "/sca-vulnerable-function")

# 4. Fetch spans from test agent
spans = _get_span(token)

# 5. Validate SCA tags
for trace in spans:
    for span in trace:
        if span["meta"].get("_dd.sca.instrumented") == "true":
            assert span["meta"]["_dd.sca.detection_hit"] == "true"
            assert "os.path:join" in span["meta"]["_dd.sca.target"]
```

## Test Execution

### Running Tests

```bash
# Run all SCA testagent tests
pytest tests/appsec/integrations/fastapi_tests/test_sca_fastapi_testagent.py -v

# Run specific test
pytest tests/appsec/integrations/fastapi_tests/test_sca_fastapi_testagent.py::test_sca_basic_instrumentation -v

# Run parametrized tests
pytest tests/appsec/integrations/fastapi_tests/test_sca_fastapi_testagent.py::test_sca_multiple_calls -v -k "multiprocess-True"
```

### Expected Behavior

- ✅ RC payloads sent via test agent reach the uvicorn process
- ✅ Instrumentation is applied dynamically (no restart needed)
- ✅ Instrumented functions add span tags when called
- ✅ Tags are correctly formatted with qualified names
- ✅ Works with async functions, concurrent requests, nested calls

## Key Differences from Unit Tests

| Aspect | Unit Tests | Integration Tests (this file) |
|--------|------------|-------------------------------|
| **Process** | Single process | Multi-process (test + uvicorn) |
| **RC Simulation** | Mock callbacks | Test agent HTTP API |
| **Instrumentation** | Direct function calls | Cross-process via RC |
| **Validation** | Registry state | Span tags from test agent |
| **Scope** | Component-level | End-to-end |

## Pattern Inspiration

These tests follow the same pattern as:
- `tests/appsec/integrations/flask_tests/test_flask_remoteconfig.py`
  - Uses test agent for RC simulation
  - Tests cross-process RC delivery
  - Validates dynamic configuration updates

## References

- **Test Agent API**: Uses `/test/session/responses/config/path` for RC injection
- **RC Product**: `SCA_DETECTION` (from `SCA.RC_PRODUCT`)
- **RC Path Format**: `"datadog/2/SCA_DETECTION/{config_name}/config"`
- **Flask RC Tests**: `test_flask_remoteconfig.py` (pattern reference)
- **SCA Constants**: `ddtrace/appsec/_constants.py` (tag definitions)
- **Implementation**: `ddtrace/appsec/sca/` (SCA runtime instrumentation)
