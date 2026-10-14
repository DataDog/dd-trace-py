"""Legacy re-export shim for a moved module.

``ddtrace.internal.utils.time`` moved to:

``ddtrace_base.utils.time``

Keep the historical import path working for external consumers; import from
the new module instead.
"""

from ddtrace_base.utils.time import HourGlass as HourGlass  # noqa: F401
from ddtrace_base.utils.time import StopWatch as StopWatch  # noqa: F401
from ddtrace_base.utils.time import Time as Time  # noqa: F401
from ddtrace_base.utils.time import log as log  # noqa: F401
