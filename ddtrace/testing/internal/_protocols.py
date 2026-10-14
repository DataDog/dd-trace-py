"""Legacy re-export shim for a moved module.

``ddtrace.testing.internal._protocols`` moved to:

``ddtrace_testing.internal._protocols``

Keep the historical import path working for external consumers; import from
the new module instead.
"""

from ddtrace_testing.internal._protocols import (
    BackendConnectorSetupProtocol as BackendConnectorSetupProtocol,  # noqa: F401
)
from ddtrace_testing.internal._protocols import TestRunProtocol as TestRunProtocol  # noqa: F401
