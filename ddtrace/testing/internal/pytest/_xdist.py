"""Legacy re-export shim for a moved module.

``ddtrace.testing.internal.pytest._xdist`` moved to:

``ddtrace_testing.internal.pytest._xdist``

Keep the historical import path working for external consumers; import from
the new module instead.
"""

from ddtrace_testing.internal.pytest._xdist import (
    _CRASH_RETRY_STATE_WORKER_INPUT as _CRASH_RETRY_STATE_WORKER_INPUT,  # noqa: F401
)
from ddtrace_testing.internal.pytest._xdist import CrashRetryBudget as CrashRetryBudget  # noqa: F401
from ddtrace_testing.internal.pytest._xdist import XdistTestOptPlugin as XdistTestOptPlugin  # noqa: F401
from ddtrace_testing.internal.pytest._xdist import (
    read_atr_crash_retry_state as read_atr_crash_retry_state,  # noqa: F401
)
