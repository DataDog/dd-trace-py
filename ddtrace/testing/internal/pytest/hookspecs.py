"""Legacy re-export shim for a moved module.

``ddtrace.testing.internal.pytest.hookspecs`` moved to:

``ddtrace_testing.internal.pytest.hookspecs``

Keep the historical import path working for external consumers; import from
the new module instead.
"""

from ddtrace_testing.internal.pytest.hookspecs import TestOptHooks as TestOptHooks  # noqa: F401
