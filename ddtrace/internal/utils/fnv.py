"""Legacy re-export shim for a moved module.

``ddtrace.internal.utils.fnv`` moved to:

``ddtrace_base.utils.fnv``

Keep the historical import path working for external consumers; import from
the new module instead.
"""

from ddtrace_base.utils.fnv import FNV1_64_INIT as FNV1_64_INIT  # noqa: F401
from ddtrace_base.utils.fnv import FNV_64_PRIME as FNV_64_PRIME  # noqa: F401
from ddtrace_base.utils.fnv import _get_byte as _get_byte  # noqa: F401
from ddtrace_base.utils.fnv import fnv as fnv  # noqa: F401
from ddtrace_base.utils.fnv import fnv1_64 as fnv1_64  # noqa: F401
from ddtrace_base.utils.fnv import no_op as no_op  # noqa: F401
