"""Legacy re-export shim for a moved module.

``ddtrace.testing.internal.manifest`` moved to:

``ddtrace_testing.internal.manifest``

Keep the historical import path working for external consumers; import from
the new module instead.
"""

from ddtrace_testing.internal.manifest import (
    known_tests_manifest_response as known_tests_manifest_response,  # noqa: F401
)
from ddtrace_testing.internal.manifest import manifest_file_path as manifest_file_path  # noqa: F401
from ddtrace_testing.internal.manifest import settings_manifest_response as settings_manifest_response  # noqa: F401
from ddtrace_testing.internal.manifest import skippable_manifest_response as skippable_manifest_response  # noqa: F401
from ddtrace_testing.internal.manifest import (
    test_management_manifest_response as test_management_manifest_response,  # noqa: F401
)
from ddtrace_testing.internal.manifest import write_manifest_cache as write_manifest_cache  # noqa: F401
