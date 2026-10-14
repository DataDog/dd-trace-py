"""Legacy re-export shim for a moved module.

``ddtrace.internal._exceptions`` moved to:

``ddtrace_base._exceptions``

Keep the historical import path working for external consumers; import from
the new module instead.
"""

from ddtrace_base._exceptions import BlockingException as BlockingException  # noqa: F401
from ddtrace_base._exceptions import DDBlockException as DDBlockException  # noqa: F401
from ddtrace_base._exceptions import E as E  # noqa: F401
from ddtrace_base._exceptions import find_exception as find_exception  # noqa: F401
