"""Legacy re-export shim for a moved module.

``ddtrace.testing.internal.errors`` moved to:

``ddtrace_testing.internal.errors``

Keep the historical import path working for external consumers; import from
the new module instead.
"""

from ddtrace_testing.internal.errors import SetupError as SetupError  # noqa: F401
