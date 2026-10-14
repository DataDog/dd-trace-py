"""Legacy re-export shim for a moved module.

``ddtrace.internal.safety`` moved to:

``ddtrace_base.safety``

Keep the historical import path working for external consumers; import from
the new module instead.
"""

from ddtrace_base.safety import IS_312_OR_NEWER as IS_312_OR_NEWER  # noqa: F401
from ddtrace_base.safety import NoneType as NoneType  # noqa: F401
from ddtrace_base.safety import SafeObjectProxy as SafeObjectProxy  # noqa: F401
from ddtrace_base.safety import _isinstance as _isinstance  # noqa: F401
from ddtrace_base.safety import get_slots as get_slots  # noqa: F401
