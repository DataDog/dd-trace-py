# IAST Streaming Tests - Summary

## Overview
Created comprehensive IAST tests to detect segmentation faults with streaming requests/responses in FastAPI and MCP servers.

## Changes Made

### 1. Enhanced FastAPI IAST Test App (`tests/appsec/integrations/fastapi_tests/app.py`)

Added two new streaming endpoints:

#### `/iast-stream` (GET)
- Returns a `StreamingResponse` with tainted data
- Tests IAST handling of basic streaming responses
- Query parameter: `data` (gets tainted and streamed back)

#### `/iast-stream-cmdi` (POST)
- Accepts form data and triggers CMDI vulnerability
- Returns a `StreamingResponse` after executing the vulnerable command
- Form parameter: `command` (used in subprocess.Popen - vulnerable sink)

### 2. New Tests in `tests/appsec/integrations/fastapi_tests/test_iast_fastapi_testagent.py`

#### `test_iast_stream_fastapi()`
- Tests IAST with basic streaming responses
- Validates no crashes/segfaults occur with streaming
- Verifies IAST remains enabled during streaming

#### `test_iast_stream_cmdi_fastapi()`
- Tests IAST CMDI detection with streaming responses
- Validates vulnerability detection works even when response is streamed
- Confirms the vulnerability is properly reported to the test agent

### 3. New MCP Server IAST Tests (`tests/appsec/integrations/fastapi_tests/test_iast_mcp_testagent.py`)

This test suite validates IAST with MCP (Model Context Protocol) servers. MCP uses streaming internally
for client-server communication, which may expose issues with IAST's context management.

**NOTE**: These tests use in-memory MCP connections. Testing with HTTP/SSE via `streamablehttp_client` 
requires additional work to properly expose FastMCP endpoints.

#### Test Application: `tests/appsec/integrations/fastapi_tests/mcp_app.py`
Separate FastAPI app with MCP tools:
- **execute_command**: Vulnerable tool (CMDI sink) for testing
- **get_weather**: Safe tool for baseline testing
- **calculator**: Safe tool for baseline testing
- Regular HTTP endpoints for baseline comparison (`/iast-cmdi-form`, `/health`)

#### Tests:

**`test_iast_mcp_baseline()`**
- Baseline test using regular HTTP endpoint
- Validates IAST works normally with the MCP-enabled FastAPI app
- Confirms CMDI detection in standard HTTP context

**`test_iast_mcp_tool_call_safe()`**
- Tests MCP tool calls via in-memory connection with IAST enabled
- Uses safe calculator tool to validate basic MCP interaction works
- Critical for detecting segfaults with MCP tool execution

**`test_iast_mcp_vulnerable_tool()`**
- **CRITICAL TEST** - Tests vulnerable MCP tool with IAST
- Calls `execute_command` tool which triggers CMDI vulnerability
- Validates IAST can detect vulnerabilities in MCP tool execution context
- Tests the interaction between:
  - IAST taint tracking
  - MCP's internal streaming
  - Subprocess execution (vulnerability sink)

**`test_iast_mcp_multiple_tool_calls()`**
- Stress test with multiple consecutive MCP tool calls
- Detects potential memory leaks or context corruption issues
- Makes 4 calls in sequence, ending with vulnerable tool
- Tests IAST context management across multiple MCP operations

**`test_iast_mcp_header_tainting()`**
- Tests that HTTP headers are properly tainted by IAST
- Validates header values are tracked through the application
- Uses `/iast-header-echo` endpoint to verify header tainting
- Critical for detecting header-based vulnerabilities

**`test_iast_mcp_header_cmdi()`**
- Tests CMDI detection when malicious input comes from HTTP headers
- Uses `X-Command` header as the tainted source
- Validates IAST can track taint from headers through to subprocess execution
- Tests the complete taint propagation path for header-based attacks

### HTTP/SSE Streaming Tests (Critical for Segfault Detection)

**`test_iast_mcp_sse_streaming_safe_tool()`**
- Tests MCP via real HTTP/SSE streaming (not in-memory)
- Uses `sse_client` to establish bidirectional streaming connection
- Calls safe calculator tool to validate basic streaming works
- **Critical for detecting segfaults with real streaming I/O**

**`test_iast_mcp_sse_streaming_vulnerable_tool()`**
- **MOST CRITICAL TEST FOR SEGFAULTS**
- Uses real HTTP/SSE streaming to call vulnerable MCP tool
- Combines all the factors that trigger segfaults:
  - Real bidirectional streaming (not in-memory)
  - IAST taint tracking
  - MCP protocol handling over SSE
  - Vulnerable subprocess execution
- **This is the test most likely to reproduce your segfault**

**`test_iast_mcp_sse_streaming_multiple_calls()`**
- Stress test with multiple calls through same HTTP/SSE stream
- Makes 4 consecutive MCP tool calls over persistent connection
- Detects memory leaks or context corruption with streaming
- Tests IAST stability with repeated streaming operations

## How to Run the Tests

### FastAPI Streaming Tests
```bash
# Activate environment
pyenv activate ddtrace3.13

# Set environment variables
export _DD_IAST_PATCH_MODULES=benchmarks.,tests.appsec.,scripts.iast.
export DD_TRACE_AGENT_URL=http://localhost:9126
export DD_IAST_REQUEST_SAMPLING=100
export DD_IAST_DEDUPLICATION_ENABLED=false
export DD_IAST_VULNERABILITIES_PER_REQUEST=10000

# Note: Test agent runs on port 9126, not 8126

# Run streaming tests
python -m pytest -s --no-cov tests/appsec/integrations/fastapi_tests/test_iast_fastapi_testagent.py::test_iast_stream_fastapi
python -m pytest -s --no-cov tests/appsec/integrations/fastapi_tests/test_iast_fastapi_testagent.py::test_iast_stream_cmdi_fastapi
```

### MCP Tests
```bash
# Make sure testagent is running
docker compose up -d testagent

# Run MCP tests (requires mcp and fastmcp packages)
python -m pytest -s --no-cov tests/appsec/integrations/fastapi_tests/test_iast_mcp_testagent.py::test_iast_mcp_baseline
python -m pytest -s --no-cov tests/appsec/integrations/fastapi_tests/test_iast_mcp_testagent.py::test_iast_mcp_tool_call_safe
python -m pytest -s --no-cov tests/appsec/integrations/fastapi_tests/test_iast_mcp_testagent.py::test_iast_mcp_vulnerable_tool
python -m pytest -s --no-cov tests/appsec/integrations/fastapi_tests/test_iast_mcp_testagent.py::test_iast_mcp_multiple_tool_calls

# Run header tainting tests
python -m pytest -s --no-cov tests/appsec/integrations/fastapi_tests/test_iast_mcp_testagent.py::test_iast_mcp_header_tainting
python -m pytest -s --no-cov tests/appsec/integrations/fastapi_tests/test_iast_mcp_testagent.py::test_iast_mcp_header_cmdi

# Run HTTP/SSE streaming tests (CRITICAL - most likely to reproduce segfaults)
python -m pytest -s --no-cov tests/appsec/integrations/fastapi_tests/test_iast_mcp_testagent.py::test_iast_mcp_sse_streaming_safe_tool
python -m pytest -s --no-cov tests/appsec/integrations/fastapi_tests/test_iast_mcp_testagent.py::test_iast_mcp_sse_streaming_vulnerable_tool
python -m pytest -s --no-cov tests/appsec/integrations/fastapi_tests/test_iast_mcp_testagent.py::test_iast_mcp_sse_streaming_multiple_calls

# Run all MCP tests
python -m pytest -s --no-cov tests/appsec/integrations/fastapi_tests/test_iast_mcp_testagent.py
```

## Dependencies

The MCP tests require:
```bash
pip install mcp fastmcp
```

## What These Tests Detect

1. **Segmentation Faults**: Primary goal - detect crashes when IAST intercepts streaming data
2. **Memory Leaks**: Multiple calls test checks for accumulation issues
3. **Context Management**: Validates IAST context survives streaming operations
4. **Vulnerability Detection**: Confirms IAST can detect vulnerabilities through streams
5. **Taint Propagation**: Verifies taint tracking works with streaming I/O
6. **Header Tainting**: Validates that HTTP headers are properly tainted as user input sources
7. **Header-based Attacks**: Tests IAST detection when vulnerabilities originate from HTTP headers

## Expected Behavior

### If Working Correctly:
- All tests pass without segfaults
- CMDI vulnerabilities are detected and reported
- IAST remains enabled throughout streaming operations
- No memory leaks or context corruption

### If Segfault Occurs:
- Tests will crash with "Segmentation fault (core dumped)"
- Check core dump or run with debugging:
  ```bash
  export DD_TRACE_DEBUG=true
  export _DD_IAST_DEBUG=true
  export _DD_IAST_PROPAGATION_DEBUG=true
  gdb --args python -m pytest -s --no-cov tests/appsec/integrations/fastapi_tests/test_iast_mcp_testagent.py::test_iast_mcp_streamablehttp_vulnerable_tool
  ```

## Debugging Tips

If segfaults occur in the MCP tests:

1. **Enable debug mode**:
   ```bash
   export DD_TRACE_DEBUG=true
   export _DD_IAST_DEBUG=true
   ```

2. **Run with gdb**:
   ```bash
   gdb --args /home/albertovara/.pyenv/versions/3.13.9/envs/ddtrace3.13/bin/python -m pytest -s tests/appsec/integrations/fastapi_tests/test_iast_mcp_testagent.py::test_iast_mcp_vulnerable_tool
   ```

3. **Check IAST context lifecycle**:
   - Look for context creation/destruction logs
   - Verify `is_iast_request_enabled()` returns expected values
   - Check if context is properly cleaned up after streaming

4. **Isolate the issue**:
   - Start with baseline test (non-streaming)
   - Then try safe tool call (streaming but no vulnerability)
   - Finally test vulnerable tool (streaming + CMDI)

## Notes

- **AIDEV-NOTE**: The test suite includes both **in-memory MCP tests** and **HTTP/SSE streaming tests**:
  - In-memory tests use `create_connected_server_and_client_session` (faster, simpler)
  - HTTP/SSE tests use `sse_client` for real bidirectional streaming (more realistic, **critical for segfault detection**)
  - The HTTP/SSE tests are the most likely to reproduce segfaults because they use actual streaming I/O

- The async tests are marked with `@pytest.mark.asyncio` because MCP client operations are async.

- The MCP app exposes an SSE endpoint at `/iast/mcp` for HTTP/SSE streaming tests.

- The MCP app can be run independently for manual testing:
  ```bash
  uvicorn tests.appsec.integrations.fastapi_tests.mcp_app:app --reload --port 8051
  ```

- **Port Usage**: 
  - In-memory and header tests use port `8051`
  - HTTP/SSE streaming tests use port `8052`
  - This avoids port conflicts when running multiple tests
