"""Legacy re-export shim for a moved module.

``ddtrace.testing.internal.logs`` moved to:

``ddtrace_testing.internal.logs``

Keep the historical import path working for external consumers; import from
the new module instead.
"""

from ddtrace_testing.internal.logs import LogsHandler as LogsHandler  # noqa: F401
from ddtrace_testing.internal.logs import LogsWriter as LogsWriter  # noqa: F401
