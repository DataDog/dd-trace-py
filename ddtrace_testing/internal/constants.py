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

# Git metadata environment variables
DD_GIT_PULL_REQUEST_BASE_BRANCH_SHA = "DD_GIT_PULL_REQUEST_BASE_BRANCH_SHA"

# Bazel / offline mode environment variables
DD_TEST_OPTIMIZATION_MANIFEST_FILE = "DD_TEST_OPTIMIZATION_MANIFEST_FILE"
DD_TEST_OPTIMIZATION_PAYLOADS_IN_FILES = "DD_TEST_OPTIMIZATION_PAYLOADS_IN_FILES"
DD_TEST_OPTIMIZATION_ENV_DATA_FILE = "DD_TEST_OPTIMIZATION_ENV_DATA_FILE"
TEST_UNDECLARED_OUTPUTS_DIR = "TEST_UNDECLARED_OUTPUTS_DIR"

# Test discovery environment variables
DD_TEST_OPTIMIZATION_DISCOVERY_ENABLED = "DD_TEST_OPTIMIZATION_DISCOVERY_ENABLED"
DD_TEST_OPTIMIZATION_DISCOVERY_FILE = "DD_TEST_OPTIMIZATION_DISCOVERY_FILE"

# The only supported .testoptimization manifest version
SUPPORTED_MANIFEST_VERSION = 1
TEST_OPTIMIZATION_MANIFEST_FILENAME = "manifest.txt"
TEST_OPTIMIZATION_CACHE_DIR = "cache"
TEST_OPTIMIZATION_HTTP_CACHE_DIR = TEST_OPTIMIZATION_CACHE_DIR + "/http"
TEST_OPTIMIZATION_SETTINGS_FILE = "settings.json"
TEST_OPTIMIZATION_KNOWN_TESTS_FILE = "known_tests.json"
TEST_OPTIMIZATION_TEST_MANAGEMENT_FILE = "test_management.json"
TEST_OPTIMIZATION_SKIPPABLE_TESTS_FILE = "skippable_tests.json"

# Prefix of the temp directory holding a manifest cache we generated ourselves for pytest-xdist workers, as opposed to
# one provided by an external tool such as the Bazel rule. The controller pid follows the prefix.
XDIST_MANIFEST_DIR_PREFIX = "dd_xdist_manifest_"
