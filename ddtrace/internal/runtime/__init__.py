# Compatibility re-exports for existing ddtrace.internal.runtime imports.
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
