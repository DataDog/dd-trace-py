# Running SCA Integration Tests with Test Agent

## Problem Identified

The SCA integration tests in `tests/appsec/integrations/fastapi_tests/test_sca_fastapi_testagent.py` require the **Datadog Test Agent** to be running. Without it, tests fail with:

```
ConnectionRefusedError: [Errno 111] Connection refused
```

This happens when the test fixture tries to connect to `localhost:8126`.

## Solution: Start the Test Agent

### Using Docker

The test agent can be run using Docker:

```bash
# Start the test agent
docker run -d --name ddagent-test \
  -p 8126:8126 \
  ghcr.io/datadog/dd-apm-test-agent/ddapm-test-agent:latest

# Verify it's running
curl http://localhost:8126/test/session/start
```

### Using Local Installation

Alternatively, install and run the test agent locally:

```bash
# Install the test agent
pip install ddapm-test-agent

# Run it
ddapm-test-agent