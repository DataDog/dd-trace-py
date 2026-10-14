"""Legacy re-export shim for a moved module.

``ddtrace.testing.internal`` (package) moved to:

``ddtrace_testing.internal``

Keep the historical import path working for external consumers; import from
the new module instead.
"""

from ddtrace_testing.internal import ci as ci  # noqa: F401
from ddtrace_testing.internal import git as git  # noqa: F401
