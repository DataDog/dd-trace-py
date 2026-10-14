"""Legacy re-export shim for a moved module.

``ddtrace.testing.internal.api_client`` moved to:

``ddtrace_testing.internal.api_client``

Keep the historical import path working for external consumers; import from
the new module instead.
"""

from ddtrace_testing.internal.api_client import APIClient as APIClient  # noqa: F401
from ddtrace_testing.internal.api_client import log as log  # noqa: F401
