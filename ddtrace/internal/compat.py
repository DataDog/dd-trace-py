"""Legacy re-export shim for a moved module.

``ddtrace.internal.compat`` moved to:

``ddtrace_base.compat``

Keep the historical import path working for external consumers; import from
the new module instead.
"""

from ddtrace_base.compat import MAX_PY as MAX_PY  # noqa: F401
from ddtrace_base.compat import NEXT_MAX_PY as NEXT_MAX_PY  # noqa: F401
from ddtrace_base.compat import NEXT_PY_UNSUPPORTED_MSG as NEXT_PY_UNSUPPORTED_MSG  # noqa: F401
from ddtrace_base.compat import NO_EXCEPTION as NO_EXCEPTION  # noqa: F401
from ddtrace_base.compat import PYTHON_VERSION_INFO as PYTHON_VERSION_INFO  # noqa: F401
from ddtrace_base.compat import ExcInfoType as ExcInfoType  # noqa: F401
from ddtrace_base.compat import NumericType as NumericType  # noqa: F401
from ddtrace_base.compat import ensure_binary as ensure_binary  # noqa: F401
from ddtrace_base.compat import ensure_text as ensure_text  # noqa: F401
from ddtrace_base.compat import get_mp_context as get_mp_context  # noqa: F401
from ddtrace_base.compat import ip_is_global as ip_is_global  # noqa: F401
from ddtrace_base.compat import is_at_least_py as is_at_least_py  # noqa: F401
from ddtrace_base.compat import is_at_most_py as is_at_most_py  # noqa: F401
from ddtrace_base.compat import is_integer as is_integer  # noqa: F401
from ddtrace_base.compat import is_supported_python_version as is_supported_python_version  # noqa: F401
from ddtrace_base.compat import is_valid_ip as is_valid_ip  # noqa: F401
from ddtrace_base.compat import is_wrapted as is_wrapted  # noqa: F401
from ddtrace_base.compat import maybe_stringify as maybe_stringify  # noqa: F401
from ddtrace_base.compat import singledispatchmethod as singledispatchmethod  # noqa: F401
