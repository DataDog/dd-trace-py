"""Legacy re-export shim for a moved module.

``ddtrace.testing.internal.pytest.plugin`` moved to:

``ddtrace_testing.internal.pytest.plugin``

Keep the historical import path working for external consumers; import from
the new module instead.
"""

from ddtrace_testing.internal.pytest.plugin import _EXTERNAL_RERUN_PLUGINS as _EXTERNAL_RERUN_PLUGINS  # noqa: F401
from ddtrace_testing.internal.pytest.plugin import (
    _HOOKIMPL_SUPPORTS_SPECNAME as _HOOKIMPL_SUPPORTS_SPECNAME,  # noqa: F401
)
from ddtrace_testing.internal.pytest.plugin import (
    DISABLED_BY_TEST_MANAGEMENT_REASON as DISABLED_BY_TEST_MANAGEMENT_REASON,
)  # noqa: F401
from ddtrace_testing.internal.pytest.plugin import ITR_UNSKIPPABLE_REASON as ITR_UNSKIPPABLE_REASON  # noqa: F401
from ddtrace_testing.internal.pytest.plugin import (
    OUT_OF_SESSION_RETRY_PRETTY_NAME as OUT_OF_SESSION_RETRY_PRETTY_NAME,  # noqa: F401
)
from ddtrace_testing.internal.pytest.plugin import (
    OUT_OF_SESSION_RETRY_REASON as OUT_OF_SESSION_RETRY_REASON,  # noqa: F401
)
from ddtrace_testing.internal.pytest.plugin import SESSION_MANAGER_STASH_KEY as SESSION_MANAGER_STASH_KEY  # noqa: F401
from ddtrace_testing.internal.pytest.plugin import SKIPPED_BY_ITR_REASON as SKIPPED_BY_ITR_REASON  # noqa: F401
from ddtrace_testing.internal.pytest.plugin import TEST_FRAMEWORK as TEST_FRAMEWORK  # noqa: F401
from ddtrace_testing.internal.pytest.plugin import XDIST_MANIFEST_STASH_KEY as XDIST_MANIFEST_STASH_KEY  # noqa: F401
from ddtrace_testing.internal.pytest.plugin import RetryReports as RetryReports  # noqa: F401
from ddtrace_testing.internal.pytest.plugin import TestOptPlugin as TestOptPlugin  # noqa: F401
from ddtrace_testing.internal.pytest.plugin import TestOptPluginWithProtocol as TestOptPluginWithProtocol  # noqa: F401
from ddtrace_testing.internal.pytest.plugin import TestPhase as TestPhase  # noqa: F401
from ddtrace_testing.internal.pytest.plugin import _get_exception_tags as _get_exception_tags  # noqa: F401
from ddtrace_testing.internal.pytest.plugin import (
    _get_module_path_from_item as _get_module_path_from_item,  # noqa: F401
)
from ddtrace_testing.internal.pytest.plugin import (
    _get_relative_module_path_from_item as _get_relative_module_path_from_item,
)  # noqa: F401
from ddtrace_testing.internal.pytest.plugin import _get_source_lines as _get_source_lines  # noqa: F401
from ddtrace_testing.internal.pytest.plugin import _get_test_command as _get_test_command  # noqa: F401
from ddtrace_testing.internal.pytest.plugin import _get_test_location_info as _get_test_location_info  # noqa: F401
from ddtrace_testing.internal.pytest.plugin import _get_test_original_name as _get_test_original_name  # noqa: F401
from ddtrace_testing.internal.pytest.plugin import _get_user_property as _get_user_property  # noqa: F401
from ddtrace_testing.internal.pytest.plugin import _stash_set as _stash_set  # noqa: F401
from ddtrace_testing.internal.pytest.plugin import ddspan as ddspan  # noqa: F401
from ddtrace_testing.internal.pytest.plugin import ddtracer as ddtracer  # noqa: F401
from ddtrace_testing.internal.pytest.plugin import log as log  # noqa: F401
from ddtrace_testing.internal.pytest.plugin import pytest_addoption as pytest_addoption  # noqa: F401
from ddtrace_testing.internal.pytest.plugin import pytest_configure as pytest_configure  # noqa: F401
from ddtrace_testing.internal.pytest.plugin import (
    pytest_load_initial_conftests as pytest_load_initial_conftests,  # noqa: F401
)
from ddtrace_testing.internal.pytest.plugin import pytest_sessionfinish as pytest_sessionfinish  # noqa: F401
from ddtrace_testing.internal.pytest.plugin import pytest_unconfigure as pytest_unconfigure  # noqa: F401
from ddtrace_testing.internal.pytest.plugin import setup_coverage_collection as setup_coverage_collection  # noqa: F401
