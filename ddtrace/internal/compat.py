"""Bridge to the extracted ``ddtrace-internal`` package: this module *is* ``_ddtrace_internal.compat``.

At runtime the module object is replaced by the package module, so there is a single copy of its
state and tests may patch either path. The ``TYPE_CHECKING`` block only tells static tools what it exports.
"""

import sys
from typing import TYPE_CHECKING

import _ddtrace_internal.compat as _impl


if TYPE_CHECKING:  # pragma: no cover
    from _ddtrace_internal.compat import MAX_PY as MAX_PY  # noqa: F401
    from _ddtrace_internal.compat import NEXT_MAX_PY as NEXT_MAX_PY  # noqa: F401
    from _ddtrace_internal.compat import NEXT_PY_UNSUPPORTED_MSG as NEXT_PY_UNSUPPORTED_MSG  # noqa: F401
    from _ddtrace_internal.compat import NO_EXCEPTION as NO_EXCEPTION  # noqa: F401
    from _ddtrace_internal.compat import PYTHON_VERSION_INFO as PYTHON_VERSION_INFO  # noqa: F401
    from _ddtrace_internal.compat import ExcInfoType as ExcInfoType  # noqa: F401
    from _ddtrace_internal.compat import NumericType as NumericType  # noqa: F401
    from _ddtrace_internal.compat import __getattr__ as __getattr__  # noqa: F401
    from _ddtrace_internal.compat import _singledispatchmethod_register as _singledispatchmethod_register  # noqa: F401
    from _ddtrace_internal.compat import ensure_binary as ensure_binary  # noqa: F401
    from _ddtrace_internal.compat import ensure_text as ensure_text  # noqa: F401
    from _ddtrace_internal.compat import ip_is_global as ip_is_global  # noqa: F401
    from _ddtrace_internal.compat import is_at_least_py as is_at_least_py  # noqa: F401
    from _ddtrace_internal.compat import is_at_most_py as is_at_most_py  # noqa: F401
    from _ddtrace_internal.compat import is_integer as is_integer  # noqa: F401
    from _ddtrace_internal.compat import is_supported_python_version as is_supported_python_version  # noqa: F401
    from _ddtrace_internal.compat import is_valid_ip as is_valid_ip  # noqa: F401
    from _ddtrace_internal.compat import is_wrapted as is_wrapted  # noqa: F401
    from _ddtrace_internal.compat import maybe_stringify as maybe_stringify  # noqa: F401
    from _ddtrace_internal.compat import wrapt_class as wrapt_class  # noqa: F401
else:
    sys.modules[__name__] = _impl
