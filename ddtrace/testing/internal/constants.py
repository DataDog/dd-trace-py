from enum import Enum


class ITRSkippingLevel(Enum):
    SUITE = "suite"
    TEST = "test"


DEFAULT_SERVICE_NAME = "test"
DEFAULT_ENV_NAME = "none"
DEFAULT_SITE = "datadoghq.com"

DEFAULT_AGENT_HOSTNAME = "localhost"
DEFAULT_AGENT_PORT = 8126
DEFAULT_AGENT_SOCKET_FILE = "/var/run/datadog/apm.socket"

TAG_TRUE = "true"
TAG_FALSE = "false"

EMPTY_NAME = "."

# Bazel / offline mode environment variables
DD_TEST_OPTIMIZATION_MANIFEST_FILE = "DD_TEST_OPTIMIZATION_MANIFEST_FILE"
DD_TEST_OPTIMIZATION_PAYLOADS_IN_FILES = "DD_TEST_OPTIMIZATION_PAYLOADS_IN_FILES"
DD_TEST_OPTIMIZATION_ENV_DATA_FILE = "DD_TEST_OPTIMIZATION_ENV_DATA_FILE"
TEST_UNDECLARED_OUTPUTS_DIR = "TEST_UNDECLARED_OUTPUTS_DIR"

# Supported .testoptimization manifest versions.
# Version 1: Bazel offline mode — no HTTP, test skipping disabled (Bazel handles selection).
# Version 2: ddtest orchestration — no HTTP, test skipping applied from cached skippable_tests.json.
SUPPORTED_MANIFEST_VERSIONS = frozenset({1, 2})
MANIFEST_VERSION_DDTEST = 2
