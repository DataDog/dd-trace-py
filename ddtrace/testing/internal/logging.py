"""Legacy re-export shim for a moved module.

``ddtrace.testing.internal.logging`` moved to:

``ddtrace_testing.internal.logging``

Keep the historical import path working for external consumers; import from
the new module instead.
"""

from ddtrace_testing.internal.logging import F as F  # noqa: F401
from ddtrace_testing.internal.logging import _DDTraceClosedStreamFilter as _DDTraceClosedStreamFilter  # noqa: F401
from ddtrace_testing.internal.logging import _SafeStreamHandler as _SafeStreamHandler  # noqa: F401
from ddtrace_testing.internal.logging import catch_and_log_exceptions as catch_and_log_exceptions  # noqa: F401
from ddtrace_testing.internal.logging import (
    protect_ddtrace_stream_handlers as protect_ddtrace_stream_handlers,  # noqa: F401
)
from ddtrace_testing.internal.logging import setup_logging as setup_logging  # noqa: F401
from ddtrace_testing.internal.logging import testing_logger as testing_logger  # noqa: F401
