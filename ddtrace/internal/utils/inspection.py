"""Legacy re-export shim for a moved module.

``ddtrace.internal.utils.inspection`` moved to:

``ddtrace_base.utils.inspection``

Keep the historical import path working for external consumers; import from
the new module instead.
"""

from ddtrace_base.utils.inspection import ModuleCodeCollector as ModuleCodeCollector  # noqa: F401
from ddtrace_base.utils.inspection import clear as clear  # noqa: F401
from ddtrace_base.utils.inspection import collect_code_objects as collect_code_objects  # noqa: F401
from ddtrace_base.utils.inspection import functions_for_code as functions_for_code  # noqa: F401
from ddtrace_base.utils.inspection import linenos as linenos  # noqa: F401
from ddtrace_base.utils.inspection import resolved_code_origin as resolved_code_origin  # noqa: F401
from ddtrace_base.utils.inspection import undecorated as undecorated  # noqa: F401
