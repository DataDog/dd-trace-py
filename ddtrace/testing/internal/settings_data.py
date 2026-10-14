"""Legacy re-export shim for a moved module.

``ddtrace.testing.internal.settings_data`` moved to:

``ddtrace_testing.internal.settings_data``

Keep the historical import path working for external consumers; import from
the new module instead.
"""

from ddtrace_testing.internal.settings_data import AutoTestRetriesSettings as AutoTestRetriesSettings  # noqa: F401
from ddtrace_testing.internal.settings_data import (
    EarlyFlakeDetectionSettings as EarlyFlakeDetectionSettings,  # noqa: F401
)
from ddtrace_testing.internal.settings_data import Settings as Settings  # noqa: F401
from ddtrace_testing.internal.settings_data import TestManagementSettings as TestManagementSettings  # noqa: F401
from ddtrace_testing.internal.settings_data import TestProperties as TestProperties  # noqa: F401
from ddtrace_testing.internal.settings_data import log as log  # noqa: F401
