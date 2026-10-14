"""Legacy re-export shim for a moved module.

``ddtrace.testing.internal.pytest._protocols`` moved to:

``ddtrace_testing.internal.pytest._protocols``

Keep the historical import path working for external consumers; import from
the new module instead.
"""

from ddtrace_testing.internal.pytest._protocols import TestOptPluginProtocol as TestOptPluginProtocol  # noqa: F401
