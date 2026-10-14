"""Legacy re-export shim for a moved module.

``ddtrace.internal.codeowners`` moved to:

``ddtrace_base.codeowners``

Keep the historical import path working for external consumers; import from
the new module instead.
"""

from ddtrace_base.codeowners import Codeowners as Codeowners  # noqa: F401
from ddtrace_base.codeowners import path_to_regex as path_to_regex  # noqa: F401
