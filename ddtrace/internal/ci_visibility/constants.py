EVENT_TYPE = "type"


# Test Session ID
SESSION_ID = "test_session_id"

# Test Module ID
MODULE_ID = "test_module_id"

# Test Suite ID
SUITE_ID = "test_suite_id"

# Event type signals for CI Visibility
SESSION_TYPE = "test_session_end"

MODULE_TYPE = "test_module_end"

SUITE_TYPE = "test_suite_end"

# Agentless and EVP-specific constants
COVERAGE_TAG_NAME = "test.coverage"

EVP_PROXY_AGENT_BASE_PATH = "evp_proxy/v2"
EVP_PROXY_AGENT_ENDPOINT = "{}/api/v2/citestcycle".format(EVP_PROXY_AGENT_BASE_PATH)
AGENTLESS_ENDPOINT = "api/v2/citestcycle"
EVP_SUBDOMAIN_HEADER_NAME = "X-Datadog-EVP-Subdomain"
EVP_SUBDOMAIN_HEADER_VALUE = "citestcycle-intake"
AGENTLESS_BASE_URL = "https://citestcycle-intake"
AGENTLESS_DEFAULT_SITE = "datadoghq.com"
GIT_API_BASE_PATH = "/api/v2/git"
