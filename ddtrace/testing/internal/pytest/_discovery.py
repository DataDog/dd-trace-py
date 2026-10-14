"""Legacy re-export shim for a moved module.

``ddtrace.testing.internal.pytest._discovery`` moved to:

``ddtrace_testing.internal.pytest._discovery``

Keep the historical import path working for external consumers; import from
the new module instead.
"""

from ddtrace_testing.internal.pytest._discovery import (
    is_discovery_mode_enabled as is_discovery_mode_enabled,  # noqa: F401
)
from ddtrace_testing.internal.pytest._discovery import log as log  # noqa: F401
from ddtrace_testing.internal.pytest._discovery import (
    pytest_collection_finish as pytest_collection_finish,  # noqa: F401
)
