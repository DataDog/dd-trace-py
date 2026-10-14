"""Legacy re-export shim for a moved module.

``ddtrace.internal.glob_matching`` moved to:

``ddtrace_base.glob_matching``

Keep the historical import path working for external consumers; import from
the new module instead.
"""

from ddtrace_base.glob_matching import GlobMatcher as GlobMatcher  # noqa: F401
