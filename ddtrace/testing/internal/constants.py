"""Legacy re-export shim for a moved module.

``ddtrace.testing.internal.constants`` moved to:

``ddtrace_testing.internal.constants``

Keep the historical import path working for external consumers; import from
the new module instead.
"""

from ddtrace_testing.internal.constants import (
    DD_GIT_PULL_REQUEST_BASE_BRANCH_SHA as DD_GIT_PULL_REQUEST_BASE_BRANCH_SHA,
)  # noqa: F401
from ddtrace_testing.internal.constants import (
    DD_TEST_OPTIMIZATION_DISCOVERY_ENABLED as DD_TEST_OPTIMIZATION_DISCOVERY_ENABLED,
)  # noqa: F401
from ddtrace_testing.internal.constants import (
    DD_TEST_OPTIMIZATION_DISCOVERY_FILE as DD_TEST_OPTIMIZATION_DISCOVERY_FILE,
)  # noqa: F401
from ddtrace_testing.internal.constants import (
    DD_TEST_OPTIMIZATION_ENV_DATA_FILE as DD_TEST_OPTIMIZATION_ENV_DATA_FILE,  # noqa: F401
)
from ddtrace_testing.internal.constants import (
    DD_TEST_OPTIMIZATION_MANIFEST_FILE as DD_TEST_OPTIMIZATION_MANIFEST_FILE,  # noqa: F401
)
from ddtrace_testing.internal.constants import (
    DD_TEST_OPTIMIZATION_PAYLOADS_IN_FILES as DD_TEST_OPTIMIZATION_PAYLOADS_IN_FILES,
)  # noqa: F401
from ddtrace_testing.internal.constants import DEFAULT_AGENT_HOSTNAME as DEFAULT_AGENT_HOSTNAME  # noqa: F401
from ddtrace_testing.internal.constants import DEFAULT_AGENT_PORT as DEFAULT_AGENT_PORT  # noqa: F401
from ddtrace_testing.internal.constants import DEFAULT_AGENT_SOCKET_FILE as DEFAULT_AGENT_SOCKET_FILE  # noqa: F401
from ddtrace_testing.internal.constants import DEFAULT_ENV_NAME as DEFAULT_ENV_NAME  # noqa: F401
from ddtrace_testing.internal.constants import DEFAULT_SERVICE_NAME as DEFAULT_SERVICE_NAME  # noqa: F401
from ddtrace_testing.internal.constants import DEFAULT_SITE as DEFAULT_SITE  # noqa: F401
from ddtrace_testing.internal.constants import EMPTY_NAME as EMPTY_NAME  # noqa: F401
from ddtrace_testing.internal.constants import SUPPORTED_MANIFEST_VERSION as SUPPORTED_MANIFEST_VERSION  # noqa: F401
from ddtrace_testing.internal.constants import TAG_FALSE as TAG_FALSE  # noqa: F401
from ddtrace_testing.internal.constants import TAG_TRUE as TAG_TRUE  # noqa: F401
from ddtrace_testing.internal.constants import TEST_OPTIMIZATION_CACHE_DIR as TEST_OPTIMIZATION_CACHE_DIR  # noqa: F401
from ddtrace_testing.internal.constants import (
    TEST_OPTIMIZATION_HTTP_CACHE_DIR as TEST_OPTIMIZATION_HTTP_CACHE_DIR,  # noqa: F401
)
from ddtrace_testing.internal.constants import (
    TEST_OPTIMIZATION_KNOWN_TESTS_FILE as TEST_OPTIMIZATION_KNOWN_TESTS_FILE,  # noqa: F401
)
from ddtrace_testing.internal.constants import (
    TEST_OPTIMIZATION_MANIFEST_FILENAME as TEST_OPTIMIZATION_MANIFEST_FILENAME,
)  # noqa: F401
from ddtrace_testing.internal.constants import (
    TEST_OPTIMIZATION_SETTINGS_FILE as TEST_OPTIMIZATION_SETTINGS_FILE,  # noqa: F401
)
from ddtrace_testing.internal.constants import (
    TEST_OPTIMIZATION_SKIPPABLE_TESTS_FILE as TEST_OPTIMIZATION_SKIPPABLE_TESTS_FILE,
)  # noqa: F401
from ddtrace_testing.internal.constants import (
    TEST_OPTIMIZATION_TEST_MANAGEMENT_FILE as TEST_OPTIMIZATION_TEST_MANAGEMENT_FILE,
)  # noqa: F401
from ddtrace_testing.internal.constants import TEST_UNDECLARED_OUTPUTS_DIR as TEST_UNDECLARED_OUTPUTS_DIR  # noqa: F401
from ddtrace_testing.internal.constants import XDIST_MANIFEST_DIR_PREFIX as XDIST_MANIFEST_DIR_PREFIX  # noqa: F401
from ddtrace_testing.internal.constants import ITRSkippingLevel as ITRSkippingLevel  # noqa: F401
