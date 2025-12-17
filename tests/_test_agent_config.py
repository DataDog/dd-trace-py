"""
Central configuration for test agent URLs.

In containerized environments (like Antithesis testing), the test agent
may not be at localhost. Set these environment variables to override:

- DD_TEST_AGENT_URL: URL for the test agent (default: http://localhost:9126)
- DD_TRACE_AGENT_URL: URL for the trace agent (already supported by ddtrace)
"""

import os


# Test agent URL - used by tests that need to communicate with ddapm-test-agent
# Default matches the standard docker-compose.yml testagent port mapping
TEST_AGENT_URL = os.environ.get("DD_TEST_AGENT_URL", "http://localhost:9126")

# For tests that create writers directly, this is the agent URL to use
# Falls back to DD_TRACE_AGENT_URL if set, otherwise localhost:8126
AGENT_URL = os.environ.get("DD_TRACE_AGENT_URL", "http://localhost:8126")

