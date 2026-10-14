"""Legacy re-export shim for a moved module.

``ddtrace.testing.internal.pytest.bdd`` moved to:

``ddtrace_testing.internal.pytest.bdd``

Keep the historical import path working for external consumers; import from
the new module instead.
"""

from ddtrace_testing.internal.pytest.bdd import FRAMEWORK as FRAMEWORK  # noqa: F401
from ddtrace_testing.internal.pytest.bdd import STEP_KIND as STEP_KIND  # noqa: F401
from ddtrace_testing.internal.pytest.bdd import BddTestOptPlugin as BddTestOptPlugin  # noqa: F401
from ddtrace_testing.internal.pytest.bdd import _get_step_func_args_json as _get_step_func_args_json  # noqa: F401
from ddtrace_testing.internal.pytest.bdd import log as log  # noqa: F401
