"""Legacy re-export shim for a moved module.

``ddtrace.testing.internal.tracer_api.coverage`` moved to:

``ddtrace_testing.internal.tracer_api.coverage``

Keep the historical import path working for external consumers; import from
the new module instead.
"""

from ddtrace_testing.internal.tracer_api.coverage import CoverageData as CoverageData  # noqa: F401
from ddtrace_testing.internal.tracer_api.coverage import coverage_collection as coverage_collection  # noqa: F401
from ddtrace_testing.internal.tracer_api.coverage import (
    get_coverage_percentage as get_coverage_percentage,  # noqa: F401
)
from ddtrace_testing.internal.tracer_api.coverage import install_coverage as install_coverage  # noqa: F401
from ddtrace_testing.internal.tracer_api.coverage import (
    install_coverage_percentage as install_coverage_percentage,  # noqa: F401
)
from ddtrace_testing.internal.tracer_api.coverage import log as log  # noqa: F401
from ddtrace_testing.internal.tracer_api.coverage import uninstall_coverage as uninstall_coverage  # noqa: F401
from ddtrace_testing.internal.tracer_api.coverage import (
    uninstall_coverage_percentage as uninstall_coverage_percentage,  # noqa: F401
)
