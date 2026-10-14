"""Legacy re-export shim for a moved module.

``ddtrace.testing.internal.platform`` moved to:

``ddtrace_testing.internal.platform``

Keep the historical import path working for external consumers; import from
the new module instead.
"""

from ddtrace_testing.internal.platform import PlatformTag as PlatformTag  # noqa: F401
from ddtrace_testing.internal.platform import get_platform_tags as get_platform_tags  # noqa: F401
