"""Legacy re-export shim for a moved module.

``ddtrace.internal.assembly`` moved to:

``ddtrace_base.assembly``

Keep the historical import path working for external consumers; import from
the new module instead.
"""

from ddtrace_base.assembly import Assembly as Assembly  # noqa: F401
from ddtrace_base.assembly import BindOpArg as BindOpArg  # noqa: F401
from ddtrace_base.assembly import relocate as relocate  # noqa: F401
from ddtrace_base.assembly import transform_instruction as transform_instruction  # noqa: F401
