"""Legacy re-export shim for a moved module.

``ddtrace.testing.internal.session_manager`` moved to:

``ddtrace_testing.internal.session_manager``

Keep the historical import path working for external consumers; import from
the new module instead.
"""

from ddtrace_testing.internal.session_manager import SessionManager as SessionManager  # noqa: F401
from ddtrace_testing.internal.session_manager import log as log  # noqa: F401
