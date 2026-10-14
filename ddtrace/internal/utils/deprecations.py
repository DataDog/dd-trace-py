"""
Legacy import path for ``ddtrace.internal.utils.deprecations``, which moved to ``ddtrace_base.utils.deprecations``.

This module is a thin re-export shim so external consumers of the historical
``ddtrace.internal.utils.deprecations`` import path keep working..
Import from ````ddtrace_base.utils.deprecations```` instead.
"""

from ddtrace_base.utils.deprecations import DDTraceDeprecationWarning as DDTraceDeprecationWarning  # noqa: F401
from ddtrace_base.utils.deprecations import deprecate as deprecate  # noqa: F401
from ddtrace_base.utils.deprecations import generate_message as generate_message  # noqa: F401
