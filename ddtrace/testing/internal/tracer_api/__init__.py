"""Legacy re-export shim for a moved module.

``ddtrace.testing.internal.tracer_api`` (package) moved to:

``ddtrace_testing.internal.tracer_api``

Keep the historical import path working for external consumers; import from
the new module instead.
"""

from ddtrace_testing.internal.tracer_api import Codeowners as Codeowners  # noqa: F401
from ddtrace_testing.internal.tracer_api import StopWatch as StopWatch  # noqa: F401
from ddtrace_testing.internal.tracer_api import Time as Time  # noqa: F401
from ddtrace_testing.internal.tracer_api import msgpack_packb as msgpack_packb  # noqa: F401
from ddtrace_testing.internal.tracer_api import rand64bits as rand64bits  # noqa: F401
