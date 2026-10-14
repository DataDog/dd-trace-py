"""Compatibility shim: the public log-correlation API moved to ``ddtrace_testing.logs``.

``ddtrace.testing.logs`` remains a supported import path.
"""

from ddtrace_testing.logs import CorrelationFilter  # noqa: F401
from ddtrace_testing.logs import DDTestLogsHandler  # noqa: F401
from ddtrace_testing.logs import ThreadLocalCorrelationFilter  # noqa: F401
