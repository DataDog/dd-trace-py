"""Legacy re-export shim for a moved module.

``ddtrace.testing.internal.tracer_api.context`` moved to:

``ddtrace_testing.internal.tracer_api.context``

Keep the historical import path working for external consumers; import from
the new module instead.
"""

from ddtrace_testing.internal.tracer_api.context import (
    enable_all_ddtrace_integrations as enable_all_ddtrace_integrations,
)  # noqa: F401
from ddtrace_testing.internal.tracer_api.context import (
    install_global_trace_filter as install_global_trace_filter,  # noqa: F401
)
from ddtrace_testing.internal.tracer_api.context import log as log  # noqa: F401
from ddtrace_testing.internal.tracer_api.context import trace_context as trace_context  # noqa: F401
from ddtrace_testing.internal.tracer_api.context import (
    uninstall_global_trace_filter as uninstall_global_trace_filter,  # noqa: F401
)
