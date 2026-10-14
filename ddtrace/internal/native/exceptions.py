"""Legacy import path for ``ddtrace.internal.native.exceptions``, which moved to ``ddtrace_base.pyo3``.

Thin re-export shim so external consumers of the historical import path keep working.
Import from ``ddtrace_base.pyo3`` instead.
"""

from ddtrace_base.pyo3 import is_panic_exception as is_panic_exception  # noqa: F401
