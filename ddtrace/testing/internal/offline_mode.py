"""Legacy re-export shim for a moved module.

``ddtrace.testing.internal.offline_mode`` moved to:

``ddtrace_testing.internal.offline_mode``

Keep the historical import path working for external consumers; import from
the new module instead.
"""

from ddtrace_testing.internal.offline_mode import OfflineMode as OfflineMode  # noqa: F401
from ddtrace_testing.internal.offline_mode import _parse_manifest_version as _parse_manifest_version  # noqa: F401
from ddtrace_testing.internal.offline_mode import _validate_manifest as _validate_manifest  # noqa: F401
from ddtrace_testing.internal.offline_mode import get_offline_mode as get_offline_mode  # noqa: F401
from ddtrace_testing.internal.offline_mode import log as log  # noqa: F401
from ddtrace_testing.internal.offline_mode import reset_offline_mode as reset_offline_mode  # noqa: F401
from ddtrace_testing.internal.offline_mode import resolve_rlocation as resolve_rlocation  # noqa: F401
from ddtrace_testing.internal.offline_mode import write_payload_file as write_payload_file  # noqa: F401
