import contextlib
import os
import threading
import time
import types as _types_mp
from typing import Any
from typing import NamedTuple
from typing import Optional
import uuid
import weakref

import torch

from ddtrace.internal.logger import get_logger
from ddtrace.internal.settings import env


log = get_logger(__name__)

_bypass_state = threading.local()

# Thread-local timestamp of the most recent optimizer.step end, set by the
# Layer 2 hooks and read on the next forward to emit `pytorch.data_load`.
_LAST_OPTIMIZER_STEP_END_NS = threading.local()

# Thread-local AMP state set by the GradScaler wrapper:
#   in_amp:        True while inside ``scaler.step(optimizer)``
#   step_executed: True if the inner optimizer.step actually ran (i.e. no AMP overflow)
_amp_skip_state = threading.local()


def is_amp_step_in_progress() -> bool:
    return getattr(_amp_skip_state, "in_amp", False)


_CLOCK_DRIFT_THRESHOLD_NS = 1_000_000  # 1 ms
_CLOCK_OFFSET_SAMPLES = 5

_JOB_ID_ENV_CHAIN = (
    "DD_PYTORCH_JOB_ID",  # explicit user override
    "RAY_JOB_ID",  # Ray Train / Tune — preferred so Ray-driven traces are consistent
    "TORCHELASTIC_RUN_ID",  # torch.distributed.elastic / torchrun
    "KUBEFLOW_TRAINING_JOB_ID",  # Kubeflow Training Operator
    "SLURM_JOB_ID",  # SLURM
)

# Datadog span-tag values are clipped at the intake; bound the resolved job_id
# to a generous limit and strip whitespace to avoid silent truncation noise
# from scheduler-supplied identifiers (SLURM/elastic IDs sometimes have trailing
# newlines).
_JOB_ID_MAX_LEN = 200

# Canonical Datadog tag used by the "ML Training Jobs" entity to group all
# spans belonging to one training run. Set on every PyTorch and Ray Train
# span. `job_id` is kept set in parallel for back-compat with dashboards
# created before this tag existed.
TRAINING_JOB_ID_TAG = "training_job.id"

# Process-wide default cache of the resolved job id. Populated by
# `_distributed._bootstrap_distributed`. Reads fall back to this when no
# thread-local override is set.
_default_job_id: Optional[str] = None

# Thread-local override. Ray Train sets this per worker fn so concurrent
# workers with different job ids don't trample each other.
_tls_job_id = threading.local()


def set_cached_job_id(value: Optional[str], *, is_default: bool = False) -> None:
    """Cache the resolved training job id so non-distributed emitters can
    tag spans without re-walking the env chain.

    AIDEV-NOTE: Pass ``is_default=True`` only from the canonical bootstrap
    (``_distributed._bootstrap_distributed``) to seed the process-wide
    default. All other callers (Ray Train workers, tests) write only to a
    thread-local override; without ``is_default``, the first caller would
    permanently pin the default to its (possibly per-worker) value.
    """
    global _default_job_id
    _tls_job_id.value = value
    if is_default:
        _default_job_id = value


def get_cached_job_id() -> Optional[str]:
    val = getattr(_tls_job_id, "value", None)
    if val is not None:
        return val
    return _default_job_id


def get_rank() -> int:
    """Read the bootstrap-cached rank from `_distributed._state["rank"]`.

    Lazy-imports `_distributed` to avoid a circular import (_distributed
    imports symbols from this module). Falls back to 0 (single-rank /
    not-yet-bootstrapped) when distributed init hasn't completed yet so
    spans that fire before `init_process_group` still carry a valid
    `rank` metric instead of being silently un-tagged.
    """
    try:
        from ddtrace.contrib.internal.pytorch._distributed import _state  # noqa: PLC0415

        return int(_state.get("rank", 0) or 0)
    except Exception:
        return 0


def set_training_job_id_tag(span) -> None:
    """Tag `span` with both `training_job.id` (new, canonical) and
    `job_id` (legacy, back-compat), and pin the trace to USER_KEEP so
    base sampling can't drop pytorch spans.

    Also propagates the Ray run context (``ray.submission_id``,
    ``ray.metadata.job_name``) when the driver-side Ray wrapper has
    populated the run-metadata cache. This lets the UI correlate
    pytorch step/kernel spans back to a Ray submission directly,
    without bridging through ``ray.train.fit``.

    The ``manual.keep`` tag sets _sampling_priority_v1 = 2 at flush
    time; since pytorch.rank carries the same id and is rare (one per
    rank, lifetime span), losing it to head-based sampling would
    permanently lose the per-rank anchor that lets metric queries
    correlate to a run via the rank-span time range.

    `training_job.id` / `job_id` are only emitted when a job id is
    cached. ``manual.keep`` and Ray run-context tags are emitted
    regardless — they remain useful for trace correlation and retention
    even when no cross-rank job id is resolvable (the post-broadcast-
    removal default when no env id is set).

    Never raises; tag-setting failures are swallowed because
    instrumentation must not crash user code.
    """
    job_id = get_cached_job_id()
    if job_id:
        try:
            span.set_tag(TRAINING_JOB_ID_TAG, job_id)
            span.set_tag("job_id", job_id)
        except Exception:
            log.debug("pytorch: failed to set training_job.id tag", exc_info=True)
    # manual.keep is the dd-trace convention for "always retain this
    # trace". Set unconditionally: even when no shared job id resolved
    # (e.g., no DD_PYTORCH_JOB_ID/RAY_JOB_ID/etc.), the trace itself
    # still carries the operator's pytorch.rank anchor and dropping it
    # to base sampling would lose the per-rank correlation window.
    try:
        span.set_tag("manual.keep")
    except Exception:
        log.debug("pytorch: failed to set manual.keep tag", exc_info=True)
    # Ray run context — best-effort, independent of job_id. The Ray
    # Train wrapper populates the cache even when no env job id is
    # set; dropping these tags whenever job_id is None would lose
    # ray.submission_id / ray.metadata correlation for the entire
    # no-env-id path that the broadcast removal made the default.
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


class ClockOffset(NamedTuple):
    offset_ns: int
    uncertainty_ns: int


def is_instrumentation_bypassed() -> bool:
    return getattr(_bypass_state, "depth", 0) > 0


def get_last_optimizer_step_end_ns() -> int:
    return getattr(_LAST_OPTIMIZER_STEP_END_NS, "value", 0)


def set_last_optimizer_step_end_ns(value_ns: int) -> None:
    _LAST_OPTIMIZER_STEP_END_NS.value = value_ns


def now_ns() -> int:
    return time.time_ns()


@contextlib.contextmanager
def _instrumentation_bypass():
    depth = getattr(_bypass_state, "depth", 0)
    _bypass_state.depth = depth + 1
    try:
        yield
    finally:
        _bypass_state.depth = depth


# AIDEV-NOTE: ``_should_record_cuda_event`` is called on every wrapped
# collective. The decision is a pure function of
# ``(group, tensor.device, tensor.dtype)`` and never changes after
# warm-up, so the cached wrapper below memoises by ``(id(group), device,
# dtype)``. The uncached form remains exported so tests / direct callers
# that need a fresh probe (e.g. mocked ``torch.cuda.is_available``) can
# bypass the cache.
_CUDA_EVENT_DECISION_CACHE: dict[tuple, bool] = {}


def _clear_cuda_event_decision_cache() -> None:
    _CUDA_EVENT_DECISION_CACHE.clear()


def _should_record_cuda_event(group, tensor) -> bool:
    # Peek into list/tuple tensors once here so the cache key uses the
    # first element's device/dtype (collectives' nested args are
    # homogeneous in practice).
    if isinstance(tensor, (list, tuple)):
        first = tensor[0] if tensor else None
    else:
        first = tensor
    if first is None:
        return False
    device = getattr(first, "device", None)
    dtype = getattr(first, "dtype", None)
    key = (id(group), device, dtype)
    decision = _CUDA_EVENT_DECISION_CACHE.get(key)
    if decision is None:
        decision = _should_record_cuda_event_uncached(group, first)
        _CUDA_EVENT_DECISION_CACHE[key] = decision
    return decision


def _should_record_cuda_event_uncached(group, tensor) -> bool:
    if not torch.cuda.is_available():
        return False
    # Some collectives (e.g. all_gather, reduce_scatter) take a list of tensors;
    # peek at the first element to decide.
    if isinstance(tensor, (list, tuple)):
        tensor = tensor[0] if tensor else None
    if tensor is None or not getattr(tensor, "is_cuda", False):
        return False
    try:
        backend = torch.distributed.get_backend(group)
    except Exception:
        return False
    return backend not in ("gloo", "mpi")


def compute_clock_offset_ns(samples: int = _CLOCK_OFFSET_SAMPLES) -> ClockOffset:
    """Offset such that ``perf_counter_ns + offset ≈ time_ns``.

    Uses min-error sandwich sampling: each measurement reads
    ``perf_counter_ns`` before and after a single ``time_ns`` read; the
    ``time_ns`` value is anchored to the midpoint of the two ``perf`` reads,
    and ``(p2 - p1) // 2`` bounds the uncertainty. The best (smallest
    uncertainty) sample of ``samples`` measurements is returned.

    Logs a warning if the best uncertainty exceeds
    ``_CLOCK_DRIFT_THRESHOLD_NS`` (typically because the thread was preempted
    between reads, or the system is under heavy load).
    """
    best_offset = 0
    best_uncertainty: Optional[int] = None
    for _ in range(max(1, samples)):
        p1 = time.perf_counter_ns()
        w = time.time_ns()
        p2 = time.perf_counter_ns()
        offset = w - (p1 + p2) // 2
        uncertainty = (p2 - p1) // 2
        if best_uncertainty is None or uncertainty < best_uncertainty:
            best_offset = offset
            best_uncertainty = uncertainty
    assert best_uncertainty is not None  # loop runs at least once
    if best_uncertainty > _CLOCK_DRIFT_THRESHOLD_NS:
        log.warning(
            "pytorch: clock offset uncertainty is %d ns (>%d ns); GPU/wall correlation may be imprecise",
            best_uncertainty,
            _CLOCK_DRIFT_THRESHOLD_NS,
        )
    return ClockOffset(offset_ns=best_offset, uncertainty_ns=best_uncertainty)


_FRAMEWORK_REGISTRY: "weakref.WeakKeyDictionary[Any, str]" = weakref.WeakKeyDictionary()  # noqa: F821
_active_stack = threading.local()


def register_framework(instance, name: str) -> None:
    """Tag a model/engine instance with its framework name (ddp/fsdp/deepspeed).

    Uses a WeakKeyDictionary so a destroyed model is automatically removed.
    """
    _FRAMEWORK_REGISTRY[instance] = name


def _stack() -> list:
    s = getattr(_active_stack, "stack", None)
    if s is None:
        s = []
        _active_stack.stack = s
    return s


@contextlib.contextmanager
def _enter_framework(instance):
    """Push `instance` onto the per-thread active-framework stack."""
    _stack().append(instance)
    try:
        yield
    finally:
        _stack().pop()


def _get_active_framework() -> Optional[str]:
    """Return the framework name (ddp/fsdp/deepspeed) of the innermost
    currently-active instance, or None when no framework context is open.
    """
    s = _stack()
    if not s:
        return None
    return _FRAMEWORK_REGISTRY.get(s[-1])


def resolve_job_id_from_env() -> str:
    """Walk the job_id env-var priority chain and fall back to a fresh UUID4.

    Order: `DD_PYTORCH_JOB_ID → RAY_JOB_ID → TORCHELASTIC_RUN_ID
    → KUBEFLOW_TRAINING_JOB_ID → SLURM_JOB_ID → UUID`. Values are stripped of surrounding
    whitespace and truncated to ``_JOB_ID_MAX_LEN`` characters; empty strings
    (after stripping) are treated as unset.
    """
    for var in _JOB_ID_ENV_CHAIN:
        raw = env.get(var)
        if not raw:
            continue
        value = raw.strip()
        if not value:
            continue
        return value[:_JOB_ID_MAX_LEN]
    return str(uuid.uuid4())


def job_id_env_set() -> bool:
    """True iff at least one of the env vars in `_JOB_ID_ENV_CHAIN` is set to
    a non-empty value (post-strip).

    Callers that would otherwise drive a cross-rank broadcast can use this to
    skip the broadcast: when every rank reads the same env, the broadcast is
    redundant and (worse) calls ``torch.distributed.broadcast_object_list``
    before user code has had a chance to set its CUDA device, which crashes
    NCCL with ``Duplicate GPU detected`` on multi-GPU-per-node setups.
    """
    for var in _JOB_ID_ENV_CHAIN:
        raw = env.get(var)
        if raw and raw.strip():
            return True
    return False


# Optional run-context fields tagged on pytorch.rank and on Ray Train's
# root spans. Populated by the Ray Train wrapper (which has driver-side
# access to the user's ``RunConfig`` and Ray's submission metadata) and
# read by the PyTorch contrib at rank-span build time.

# Writer-visible state. Updated under `_run_metadata_lock`. Readers
# should NOT consult these directly; they read `_run_metadata_view`.
_run_metadata: dict = {}
_run_metadata_lock = threading.Lock()

# Lock-free read-side: an IMMUTABLE view (MappingProxyType) over a
# dict snapshot. Writers build a new dict + proxy under the lock and
# atomically rebind this module attribute. Module-attribute assignment
# is GIL-atomic on CPython, so concurrent readers see either the
# previous or the new proxy — never a torn state.
#
# `set_training_job_id_tag` is called on every emitted span; reading
# under a lock would multiply by span rate. The MappingProxyType wrap
# also prevents accidental mutation by callers (TypeError on item
# assignment) — important because the proxy is shared across threads.
_run_metadata_view: _types_mp.MappingProxyType = _types_mp.MappingProxyType({})


def _publish_view_locked() -> None:
    """Rebuild and atomically replace `_run_metadata_view`. Caller MUST
    hold `_run_metadata_lock`.
    """
    global _run_metadata_view
    raw: dict = {}
    rn = _run_metadata.get("run_name")
    sub = _run_metadata.get("submission_id")
    md = _run_metadata.get("metadata") or {}
    if rn is not None:
        raw["run_name"] = rn
    if sub is not None:
        raw["submission_id"] = sub
    if md:
        raw["metadata"] = _types_mp.MappingProxyType(dict(md))
    _run_metadata_view = _types_mp.MappingProxyType(raw)


def set_cached_run_metadata(
    *,
    run_name: Optional[str] = None,
    submission_id: Optional[str] = None,
    metadata: Optional[dict] = None,
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


def get_cached_run_metadata() -> "_types_mp.MappingProxyType":
    """Lock-free read of the latest published snapshot.

    Returns a `MappingProxyType` — read-only at runtime. Callers that
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


def get_run_metadata_snapshot() -> dict:
    """Return a snapshot suitable for later restore. Deep-copies the
    nested `metadata` dict so callers cannot mutate live cache state.
    """
    with _run_metadata_lock:
        return {
            "run_name": _run_metadata.get("run_name"),
            "submission_id": _run_metadata.get("submission_id"),
            "metadata": dict(_run_metadata.get("metadata") or {}),
        }


def restore_run_metadata_snapshot(snapshot: dict) -> None:
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
    """Reset run-metadata cache after fork() (module-local handler).

    Mirrors the fork handlers in `_distributed.py`, `_metrics.py`, and
    `_rank_root.py`. Without this, a reused Ray worker spawned via fork
    would inherit the parent's `run_name`, `submission_id`, and
    `metadata`.
    """
    global _run_metadata, _run_metadata_lock, _run_metadata_view
    _run_metadata = {}
    _run_metadata_lock = threading.Lock()
    _run_metadata_view = _types_mp.MappingProxyType({})
    # AIDEV-NOTE: Drop the cached ``_should_record_cuda_event`` decisions
    # — entries are keyed on ``id(group)`` from the parent process and
    # are stale after fork.
    _clear_cuda_event_decision_cache()


if hasattr(os, "register_at_fork"):
    os.register_at_fork(after_in_child=_reset_child_state)
