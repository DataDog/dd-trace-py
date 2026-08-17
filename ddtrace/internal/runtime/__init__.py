# The runtime-id/process-identity helpers below live in ddtrace.internal._identity, not here:
# ddtrace.internal.runtime is zoned as product:runtime (Runtime Metrics), but these helpers are
# depended on by internal-core and other products (telemetry, remote config, the trace writer,
# tracer, debugger, CI Visibility), which aren't allowed to depend on product code. Re-exporting
# them here keeps every existing `from ddtrace.internal.runtime import ...` call site working.
from ddtrace.internal._identity import MICROVM_RUN_HOOK_METHOD  # noqa: F401
from ddtrace.internal._identity import MICROVM_RUN_HOOK_PATH  # noqa: F401
from ddtrace.internal._identity import get_ancestor_runtime_id  # noqa: F401
from ddtrace.internal._identity import get_parent_runtime_id  # noqa: F401
from ddtrace.internal._identity import get_process_role  # noqa: F401
from ddtrace.internal._identity import get_runtime_id  # noqa: F401
from ddtrace.internal._identity import get_runtime_propagation_envs  # noqa: F401
from ddtrace.internal._identity import maybe_refresh_identity  # noqa: F401
from ddtrace.internal._identity import on_runtime_id_change  # noqa: F401
from ddtrace.internal._identity import refresh_identity  # noqa: F401


__all__ = [
    "get_ancestor_runtime_id",
    "get_process_role",
    "get_runtime_id",
    "get_parent_runtime_id",
    "get_runtime_propagation_envs",
    "refresh_identity",
    "maybe_refresh_identity",
]
