import os
import threading
import types as _types_mp
from typing import Any
from typing import Optional
import uuid

from ddtrace.internal.logger import get_logger
from ddtrace.internal.settings import env


log = get_logger(__name__)

_JOB_ID_ENV_CHAIN = (
    "DD_PYTORCH_JOB_ID",  # explicit user override — wins over all launchers
    "RAY_JOB_ID",  # Ray Train / Tune — preferred so Ray-driven traces are consistent
    "TORCHELASTIC_RUN_ID",  # torch.distributed.elastic / torchrun
    "KUBEFLOW_TRAINING_JOB_ID",  # Kubeflow Training Operator
    "SLURM_JOB_ID",  # SLURM
)

# Generous limit avoids silent intake truncation; strip whitespace from scheduler IDs.
_JOB_ID_MAX_LEN = 200

# `job_id` is a legacy alias kept for dashboard back-compat.
TRAINING_JOB_ID_TAG = "training_job.id"

# Process-wide job-id cache; thread-local overrides take precedence.
_default_job_id: Optional[str] = None

# Thread-local override for multi-worker isolation.
_tls_job_id = threading.local()


def set_cached_job_id(value: Optional[str], *, is_default: bool = False) -> None:
    """Cache the resolved training job id so non-distributed emitters can tag spans.

    Pass ``is_default=True`` only from ``_distributed._bootstrap_distributed`` to seed
    the process-wide default. Other callers write only to a thread-local override so
    concurrent Ray Train workers with different job ids don't trample each other.
    """
    global _default_job_id
    _tls_job_id.value = value
    if is_default:
        _default_job_id = value


def get_cached_job_id() -> Optional[str]:
    val: Optional[str] = getattr(_tls_job_id, "value", None)
    if val is not None:
        return val
    return _default_job_id


def get_rank() -> int:
    """Return bootstrap-cached rank; falls back to 0 before init_process_group fires."""
    try:
        from ddtrace.contrib.internal.pytorch._distributed import _state  # noqa: PLC0415

        return int(_state.get("rank", 0) or 0)
    except Exception:
        return 0


def set_training_job_id_tag(span: Any) -> None:
    """Tag ``span`` with training_job.id/job_id, manual.keep, and Ray run-context tags.

    Never raises; tag-setting failures are swallowed because instrumentation
    must not crash user code.
    """
    job_id = get_cached_job_id()
    if job_id:
        try:
            span.set_tag(TRAINING_JOB_ID_TAG, job_id)
            span.set_tag("job_id", job_id)
        except Exception:
            log.debug("pytorch: failed to set training_job.id tag", exc_info=True)
    try:
        span.set_tag("manual.keep")
    except Exception:
        log.debug("pytorch: failed to set manual.keep tag", exc_info=True)
    try:
        rm = get_cached_run_metadata()
        sub = rm.get("submission_id")
        if sub:
            span.set_tag("ray.submission_id", sub)
        md = rm.get("metadata") or {}
        job_name = md.get("job_name")
        if job_name:
            span.set_tag("ray.metadata.job_name", str(job_name))
    except Exception:
        log.debug("pytorch: failed to propagate ray run metadata to step span", exc_info=True)


def resolve_job_id_from_env() -> str:
    """Walk the job-id env-var chain (DD_PYTORCH_JOB_ID → RAY_JOB_ID → … → UUID fallback)."""
    for var in _JOB_ID_ENV_CHAIN:
        raw = env.get(var)
        if not raw:
            continue
        value = raw.strip()
        if not value:
            continue
        return str(value[:_JOB_ID_MAX_LEN])
    return str(uuid.uuid4())


def job_id_env_set() -> bool:
    """True iff at least one env var in ``_JOB_ID_ENV_CHAIN`` is set to a non-empty value."""
    for var in _JOB_ID_ENV_CHAIN:
        raw = env.get(var)
        if raw and raw.strip():
            return True
    return False


# Ray Train run-context cache. Writers use _run_metadata_lock; readers use the lock-free view.
_run_metadata: dict[str, Any] = {}
_run_metadata_lock = threading.Lock()
_run_metadata_view: _types_mp.MappingProxyType[str, Any] = _types_mp.MappingProxyType[str, Any]({})


def _publish_view_locked() -> None:
    """Rebuild and atomically replace `_run_metadata_view`. Caller MUST
    hold `_run_metadata_lock`.
    """
    global _run_metadata_view
    raw: dict[str, Any] = {}
    rn = _run_metadata.get("run_name")
    sub = _run_metadata.get("submission_id")
    md = _run_metadata.get("metadata") or {}
    if rn is not None:
        raw["run_name"] = rn
    if sub is not None:
        raw["submission_id"] = sub
    if md:
        raw["metadata"] = _types_mp.MappingProxyType[str, Any](dict(md))
    _run_metadata_view = _types_mp.MappingProxyType[str, Any](raw)


def set_cached_run_metadata(
    *,
    run_name: Optional[str] = None,
    submission_id: Optional[str] = None,
    metadata: Optional[dict[str, Any]] = None,
) -> None:
    """Update the run-metadata cache. None values leave the existing
    entry intact (partial updates compose).
    """
    with _run_metadata_lock:
        if run_name is not None:
            _run_metadata["run_name"] = run_name
        if submission_id is not None:
            _run_metadata["submission_id"] = submission_id
        if metadata is not None:
            _run_metadata["metadata"] = dict(metadata)
        _publish_view_locked()


def get_cached_run_metadata() -> "_types_mp.MappingProxyType[str, Any]":
    """Lock-free read of the latest published snapshot.

    Returns a `MappingProxyType[str, Any]` — read-only at runtime. Callers that
    need a mutable dict should `dict(get_cached_run_metadata())`.
    """
    return _run_metadata_view


def clear_cached_run_metadata() -> None:
    """Public helper for tests and worker-restore paths that need to
    fully reset the cache.
    """
    with _run_metadata_lock:
        _run_metadata.clear()
        _publish_view_locked()


def get_run_metadata_snapshot() -> dict[str, Any]:
    """Return a snapshot suitable for later restore. Deep-copies the
    nested `metadata` dict so callers cannot mutate live cache state.
    """
    with _run_metadata_lock:
        return {
            "run_name": _run_metadata.get("run_name"),
            "submission_id": _run_metadata.get("submission_id"),
            "metadata": dict(_run_metadata.get("metadata") or {}),
        }


def restore_run_metadata_snapshot(snapshot: dict[str, Any]) -> None:
    """Replace the cache with a previously taken snapshot. None fields
    are cleared (unconditional overwrite — unlike `set_cached_*`).
    """
    with _run_metadata_lock:
        _run_metadata.clear()
        rn = snapshot.get("run_name")
        sub = snapshot.get("submission_id")
        md = snapshot.get("metadata")
        if rn is not None:
            _run_metadata["run_name"] = rn
        if sub is not None:
            _run_metadata["submission_id"] = sub
        if md is not None:
            _run_metadata["metadata"] = dict(md)
        _publish_view_locked()


def _reset_child_state() -> None:
    global _run_metadata, _run_metadata_lock, _run_metadata_view
    _run_metadata = {}
    _run_metadata_lock = threading.Lock()
    _run_metadata_view = _types_mp.MappingProxyType[str, Any]({})
    global _default_job_id
    _default_job_id = None
    try:
        _tls_job_id.value = None
    except AttributeError:
        pass


if hasattr(os, "register_at_fork"):
    os.register_at_fork(after_in_child=_reset_child_state)
