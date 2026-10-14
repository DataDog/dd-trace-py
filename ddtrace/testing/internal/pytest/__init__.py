"""Legacy re-export shim for a moved module.

``ddtrace.testing.internal.pytest`` (package) moved to:

``ddtrace_testing.internal.pytest``

Keep the historical import path working for external consumers; import from
the new module instead.
"""

from ddtrace_testing.internal.pytest import report_links as report_links  # noqa: F401
from ddtrace_testing.internal.pytest import xdist as xdist  # noqa: F401
