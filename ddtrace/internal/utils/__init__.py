"""Legacy re-export shim for a moved module.

``ddtrace.internal.utils`` (package) moved to:

``ddtrace_base.utils``

Keep the historical import path working for external consumers; import from
the new module instead.
"""

# Legacy re-exports: this module partially moved to ``ddtrace_base.utils``.
# The legacy import path must keep working for consumers.
from ddtrace_base.utils import ArgumentError as ArgumentError  # noqa: F401
from ddtrace_base.utils import Block_config as Block_config  # noqa: F401
from ddtrace_base.utils import _get_metas_to_propagate as _get_metas_to_propagate  # noqa: F401
from ddtrace_base.utils import _human_size as _human_size  # noqa: F401
from ddtrace_base.utils import cache as cache  # noqa: F401
from ddtrace_base.utils import deprecations as deprecations  # noqa: F401
from ddtrace_base.utils import get_argument_value as get_argument_value  # noqa: F401
from ddtrace_base.utils import get_blocked as get_blocked  # noqa: F401
from ddtrace_base.utils import inspection as inspection  # noqa: F401
from ddtrace_base.utils import set_argument_value as set_argument_value  # noqa: F401
from ddtrace_base.utils import set_blocked as set_blocked  # noqa: F401
from ddtrace_base.utils import time as time  # noqa: F401
