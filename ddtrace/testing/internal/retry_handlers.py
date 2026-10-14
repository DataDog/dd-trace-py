"""Legacy re-export shim for a moved module.

``ddtrace.testing.internal.retry_handlers`` moved to:

``ddtrace_testing.internal.retry_handlers``

Keep the historical import path working for external consumers; import from
the new module instead.
"""

from ddtrace_testing.internal.retry_handlers import AttemptToFixHandler as AttemptToFixHandler  # noqa: F401
from ddtrace_testing.internal.retry_handlers import AutoTestRetriesHandler as AutoTestRetriesHandler  # noqa: F401
from ddtrace_testing.internal.retry_handlers import (
    EarlyFlakeDetectionHandler as EarlyFlakeDetectionHandler,  # noqa: F401
)
from ddtrace_testing.internal.retry_handlers import RetryHandler as RetryHandler  # noqa: F401
