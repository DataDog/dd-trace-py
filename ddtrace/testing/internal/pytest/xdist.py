"""Legacy re-export shim for a moved module.

``ddtrace.testing.internal.pytest.xdist`` moved to:

``ddtrace_testing.internal.pytest.xdist``

Keep the historical import path working for external consumers; import from
the new module instead.
"""

from ddtrace_testing.internal.pytest.xdist import XDIST_AUTO as XDIST_AUTO  # noqa: F401
from ddtrace_testing.internal.pytest.xdist import XDIST_LOGICAL as XDIST_LOGICAL  # noqa: F401
from ddtrace_testing.internal.pytest.xdist import XDIST_UNSET as XDIST_UNSET  # noqa: F401
from ddtrace_testing.internal.pytest.xdist import XdistManifest as XdistManifest  # noqa: F401
from ddtrace_testing.internal.pytest.xdist import cleanup_xdist_manifest as cleanup_xdist_manifest  # noqa: F401
from ddtrace_testing.internal.pytest.xdist import generate_xdist_manifest as generate_xdist_manifest  # noqa: F401
from ddtrace_testing.internal.pytest.xdist import is_xdist_enabled_from_args as is_xdist_enabled_from_args  # noqa: F401
from ddtrace_testing.internal.pytest.xdist import is_xdist_worker_process as is_xdist_worker_process  # noqa: F401
from ddtrace_testing.internal.pytest.xdist import log as log  # noqa: F401
from ddtrace_testing.internal.pytest.xdist import parse_worker_value as parse_worker_value  # noqa: F401
from ddtrace_testing.internal.pytest.xdist import parse_xdist_args_from_cmd as parse_xdist_args_from_cmd  # noqa: F401
from ddtrace_testing.internal.pytest.xdist import (
    resolve_inherited_manifest_env as resolve_inherited_manifest_env,  # noqa: F401
)
