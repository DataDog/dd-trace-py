"""Per-rank lifetime span for PyTorch distributed training.

Emits one ``pytorch.rank`` span per rank, open for the lifetime of the
distributed process group. Carries ``rank``, ``world_size``, ``framework``,
``training_job.id``, and device tags.

The span is rotated every ``_rotation_interval_s`` seconds (default 300)
so partial data is visible during long runs. Rotated spans carry
``_dd.was_long_running=1``.
"""

import atexit
import threading
from typing import Any
from typing import Optional

from ddtrace import config
from ddtrace import tracer
from ddtrace.contrib.internal.pytorch import _c_bridge
from ddtrace.contrib.internal.pytorch import _c_tracer
from ddtrace.contrib.internal.pytorch import _device
from ddtrace.contrib.internal.trace_utils import int_service
from ddtrace.internal import forksafe
from ddtrace.internal.logger import get_logger
from ddtrace.internal.settings import env
from ddtrace.internal.threads import Lock


log = get_logger(__name__)

_lock = Lock()
_span: Optional[Any] = None
_atexit_registered = False
_rotation_interval_s: int = 300
_rotation_timer: Optional[threading.Timer] = None
_open_kwargs: dict[str, Any] = {}
# Bundle of torch/cudnn/distributed/ray/model invariants — computed once
# at rank-span open and reused across every rotation + scheduler-bridge
# republish. Walking torch.nn.Module parameters is O(params); doing it
# on every LR step would be a real cost.
_cached_extra: dict[str, str] = {}


def _build_extra(kwargs: dict[str, Any]) -> dict[str, str]:
    fw = kwargs.get("framework")
    return _c_bridge.build_init_bundle(
        model=kwargs.get("model"),
        ray_run_name=kwargs.get("ray_run_name"),
        ray_submission_id=kwargs.get("ray_submission_id"),
        framework_already_set=bool(fw) and fw != "none",
    )


_dropped_tokens_n: int = 0
_dropped_tokens_sum: int = 0
_dropped_tokens_min: int = 0
_dropped_tokens_max: int = 0
_dropped_lock = threading.Lock()


def record_dropped_tokens(n: int) -> None:
    """User-supplied MoE dropped-token count. Only callable manually
    from MoE training scripts — there's no GPU signature for the C
    tracer to detect this on its own. Folded into the bundle on the
    next publish and stamped on every C-emitted span under
    ``train.avg_dropped_tokens.{n, min, max, mean}``.
    """
    global _dropped_tokens_n, _dropped_tokens_sum, _dropped_tokens_min, _dropped_tokens_max
    n = int(n)
    with _dropped_lock:
        if _dropped_tokens_n == 0:
            _dropped_tokens_min = n
            _dropped_tokens_max = n
        else:
            if n < _dropped_tokens_min:
                _dropped_tokens_min = n
            if n > _dropped_tokens_max:
                _dropped_tokens_max = n
        _dropped_tokens_n += 1
        _dropped_tokens_sum += n


def _drop_tokens_bundle_keys() -> dict:
    """Snapshot the running aggregate; returns string-valued bundle keys."""
    with _dropped_lock:
        if _dropped_tokens_n == 0:
            return {}
        return {
            "train.avg_dropped_tokens.n": str(_dropped_tokens_n),
            "train.avg_dropped_tokens.min": str(_dropped_tokens_min),
            "train.avg_dropped_tokens.max": str(_dropped_tokens_max),
            "train.avg_dropped_tokens.mean": str(_dropped_tokens_sum / _dropped_tokens_n),
        }


def _publish_to_c(span: Any, open_kwargs: dict, cached_extra: dict) -> None:
    """Single call site for the C parent-context bridge — every other
    code path goes through here so we have exactly one place that owns
    the bundle merge.

    Callers MUST pass pre-snapshotted ``open_kwargs`` and ``cached_extra``
    dicts taken while holding ``_lock``. Iterating module globals here
    directly would race with concurrent mutators (``set_model``,
    ``republish_with_extra_kwarg``) and raise
    ``RuntimeError: dictionary changed size during iteration``.
    """
    _c_tracer.set_parent_context(span, {**open_kwargs, **cached_extra, **_drop_tokens_bundle_keys()})


def _build_span(kwargs: dict[str, Any]) -> Optional[Any]:
    rank = kwargs["rank"]
    world_size = kwargs["world_size"]
    framework = kwargs["framework"]
    training_job_id = kwargs["training_job_id"]
    try:
        span = tracer.start_span(
            "pytorch.rank",
            service=int_service(None, config.pytorch, default="pytorch"),
            resource=f"rank={rank}/{world_size}",
            child_of=tracer.current_span() if framework == "ray" else None,
            activate=False,
        )
    except Exception:
        log.debug("pytorch: failed to open pytorch.rank span", exc_info=True)
        return None
    try:
        span._set_attribute("rank", int(rank))
        span._set_attribute("world_size", int(world_size))
        span._set_attribute("framework", framework or "none")
        span._set_attribute("component", "pytorch")
        span._set_attribute("debug.level", "0")
        if training_job_id:
            span._set_attribute("training_job.id", training_job_id)
            span._set_attribute("job_id", training_job_id)
        # Force-keep: losing this per-rank anchor to base sampling permanently
        # breaks workload attribution via the span's time range.
        span.set_tag("manual.keep")
        info = _device.get()
        if info is not None:
            span._set_attribute("device.id", info.device_id)
            span._set_attribute("device.kind", info.kind)
            span._set_attribute("host", info.hostname)
            if info.device_index is not None:
                span._set_attribute("device.index", info.device_index)
            if info.gpu_name:
                span._set_attribute("device.gpu.name", info.gpu_name)
            if info.gpu_compute_capability:
                span._set_attribute("device.gpu.compute_capability", info.gpu_compute_capability)
            if info.gpu_sm_count is not None:
                span._set_attribute("device.gpu.sm_count", info.gpu_sm_count)
            if info.gpu_total_memory_bytes is not None:
                span._set_attribute("device.gpu.total_memory_bytes", info.gpu_total_memory_bytes)
            if info.gpu_driver_version:
                span._set_attribute("device.gpu.driver_version", info.gpu_driver_version)

        try:
            import torch  # noqa: PLC0415

            torch_ver = getattr(torch, "__version__", "") or ""
            if torch_ver:
                span._set_attribute("torch.version", str(torch_ver))
            cuda_ver = getattr(getattr(torch, "version", None), "cuda", None)
            if cuda_ver:
                span._set_attribute("torch.cuda.version", str(cuda_ver))
            hip_ver = getattr(getattr(torch, "version", None), "hip", None)
            if hip_ver:
                span._set_attribute("torch.cuda.hip_version", str(hip_ver))
            try:
                nccl_ver = torch.cuda.nccl.version()
                if isinstance(nccl_ver, tuple) and nccl_ver:
                    span._set_attribute("torch.cuda.nccl_version", ".".join(str(p) for p in nccl_ver))
            except Exception:  # nosec B110
                pass
            cudnn = getattr(getattr(torch, "backends", None), "cudnn", None)
            if cudnn is not None:
                try:
                    span._set_attribute("torch.cudnn.enabled", "true" if bool(cudnn.enabled) else "false")
                except Exception:  # nosec B110
                    pass
                try:
                    span._set_attribute("torch.cudnn.benchmark", "true" if bool(cudnn.benchmark) else "false")
                except Exception:  # nosec B110
                    pass
                try:
                    span._set_attribute(
                        "torch.cudnn.deterministic",
                        "true" if bool(cudnn.deterministic) else "false",
                    )
                except Exception:  # nosec B110
                    pass
                try:
                    v = cudnn.version()
                    if isinstance(v, int):
                        span._set_attribute("torch.cudnn.version", v)
                except Exception:  # nosec B110
                    pass
            try:
                prec = torch.get_float32_matmul_precision()
                if prec:
                    span._set_attribute("torch.float32_matmul_precision", str(prec))
            except Exception:  # nosec B110
                pass
            try:
                if torch.backends.mps.is_available():
                    span._set_attribute("torch.mps.available", "true")
            except Exception:  # nosec B110
                pass
        except Exception:
            log.debug("pytorch.rank: torch invariants tagging failed", exc_info=True)

        try:
            for envvar, tag in (
                ("NCCL_DEBUG", "nccl.debug"),
                ("NCCL_SOCKET_IFNAME", "nccl.socket_ifname"),
                ("NCCL_IB_DISABLE", "nccl.ib_disable"),
                ("TORCH_NCCL_ASYNC_ERROR_HANDLING", "nccl.async_error_handling"),
                ("CUDA_VISIBLE_DEVICES", "device.cuda.visible_devices"),
                ("MASTER_ADDR", "pytorch.master_addr"),
            ):
                val = env.get(envvar)
                if val:
                    span._set_attribute(tag, str(val))
            for envvar, facet in (
                ("LOCAL_RANK", "pytorch.local_rank"),
                ("LOCAL_WORLD_SIZE", "pytorch.local_world_size"),
                ("GROUP_RANK", "pytorch.group_rank"),
                ("GROUP_WORLD_SIZE", "pytorch.group_world_size"),
                ("MASTER_PORT", "pytorch.master_port"),
            ):
                val = env.get(envvar)
                if val:
                    try:
                        span._set_attribute(facet, int(val))
                    except Exception:  # nosec B110
                        pass
        except Exception:
            log.debug("pytorch.rank: env-signal tagging failed", exc_info=True)

        try:
            from ddtrace.contrib.internal.pytorch._distributed import _detect_launcher  # noqa: PLC0415
            from ddtrace.contrib.internal.pytorch._distributed import _get_cached_backend  # noqa: PLC0415

            launcher = _detect_launcher()
            if launcher:
                span._set_attribute("launcher", launcher)
            backend = _get_cached_backend()
            if backend:
                span._set_attribute("torch.distributed.backend", backend)
        except Exception:
            log.debug("pytorch.rank: launcher/backend tagging failed", exc_info=True)

        _tag_ray_run_context(span)
    except Exception:
        log.debug("pytorch: failed to tag pytorch.rank span", exc_info=True)
    return span


def _tag_ray_run_context(span: Any) -> None:
    """Apply Ray Train run-context tags from the pytorch-utils cache. Best-effort and idempotent."""
    try:
        from ddtrace.contrib.internal.pytorch._utils import get_cached_run_metadata  # noqa: PLC0415

        rm = get_cached_run_metadata()
        rn = rm.get("run_name")
        sub = rm.get("submission_id")
        md = rm.get("metadata") or {}
        if rn:
            span._set_attribute("ray.train.run_name", rn)
        if sub:
            span._set_attribute("ray.submission_id", sub)
        for k, v in md.items():
            try:
                span._set_attribute(f"ray.metadata.{k}", str(v))
            except Exception:
                log.debug("pytorch.rank: failed to set metadata tag %s", k, exc_info=True)
    except Exception:
        log.debug("pytorch.rank: failed to apply Ray run metadata", exc_info=True)


def retag_ray_run_context() -> None:
    """Re-apply Ray Train run-context tags to the currently-open ``pytorch.rank`` span.

    Called immediately after the pytorch-utils run-metadata cache is populated
    so the long-running rank span carries ``ray.submission_id`` for its full
    lifetime rather than only at close (when the cache may have been cleared).
    No-op when no rank span is open.
    """
    with _lock:
        span = _span
    if span is None:
        return
    try:
        _tag_ray_run_context(span)
    except Exception:
        log.debug("pytorch.rank: retag_ray_run_context failed", exc_info=True)


def _schedule_rotation() -> None:
    """Start the next rotation timer. Must be called while holding _lock."""
    global _rotation_timer
    t = threading.Timer(_rotation_interval_s, _rotate_span)
    t.daemon = True
    t.name = "dd-pytorch-rank-rotation"
    t.start()
    _rotation_timer = t


def _rotate_span() -> None:
    """Finish the current rank span and open a fresh one. Called by the rotation timer."""
    global _span, _rotation_timer

    with _lock:
        old_span = _span
        if old_span is None:
            return
        kwargs_snap = dict(_open_kwargs)
        extra_snap = dict(_cached_extra)

    new_span = _build_span(kwargs_snap)

    with _lock:
        if _span is not old_span:
            # Span was closed or replaced while we were building — discard.
            if new_span is not None:
                try:
                    new_span.finish()
                except Exception:  # nosec B110
                    pass
            return
        _span = new_span
        _schedule_rotation()

    # Point C tracer at the new span BEFORE finishing the old one —
    # ensures no gap in coverage for GPU-level root spans. The cached
    # bundle survives the rotation; only the (trace_id, span_id) part
    # of the parent-context payload actually changes.
    if new_span is not None:
        _publish_to_c(new_span, kwargs_snap, extra_snap)

    try:
        old_span._set_attribute("_dd.was_long_running", 1)
        _tag_ray_run_context(old_span)
        old_span.finish()
    except Exception:
        log.debug("pytorch: span rotation finish failed", exc_info=True)

    try:
        _safe_flush(tracer)
    except Exception:
        log.debug("pytorch: span rotation flush failed", exc_info=True)


def open_rank_span(rank: int, world_size: int, framework: str, training_job_id: Optional[str]) -> None:
    """Open the per-rank lifetime span. Idempotent — second call is a no-op."""
    global _span, _atexit_registered, _open_kwargs, _cached_extra
    with _lock:
        if _span is not None:
            return
        if not _atexit_registered:
            atexit.register(close)
            _atexit_registered = True
        _open_kwargs = {
            "rank": rank,
            "world_size": world_size,
            "framework": framework,
            "training_job_id": training_job_id,
        }
        _cached_extra = _build_extra(_open_kwargs)
        kwargs_snap = dict(_open_kwargs)
        extra_snap = dict(_cached_extra)

    new_span = _build_span(kwargs_snap)

    won_race = False
    with _lock:
        if _span is None:
            _span = new_span
            won_race = True
            _schedule_rotation()
        elif new_span is not None:
            try:
                new_span.finish()
            except Exception:  # nosec B110
                pass

    if won_race and new_span is not None:
        _publish_to_c(new_span, kwargs_snap, extra_snap)


def republish_with_extra_kwarg(key: str, value: Any) -> None:
    """Push a single mutable key (e.g. ``optim.current_learning_rate``)
    through the C bridge. The C side overwrites — not merges — so every
    update must carry the full snapshot; the cached invariant bundle is
    reused so this stays cheap even at per-iter cadence.

    No-op when no rank span is open.
    """
    with _lock:
        span = _span
        if span is None:
            return
        _open_kwargs[key] = value
        kwargs_snap = dict(_open_kwargs)
        extra_snap = dict(_cached_extra)
    try:
        _publish_to_c(span, kwargs_snap, extra_snap)
    except Exception:
        log.debug("pytorch: republish_with_extra_kwarg failed", exc_info=True)


def set_framework(name: str) -> None:
    """Update the ``framework`` tag on the open ``pytorch.rank`` span."""
    if not name:
        return
    with _lock:
        span = _span
        _open_kwargs["framework"] = name
        kwargs_snap = dict(_open_kwargs)
        extra_snap = dict(_cached_extra)
    if span is None:
        return
    try:
        span._set_attribute("framework", name)
    except Exception:
        log.debug("pytorch: failed to set framework tag", exc_info=True)
    _publish_to_c(span, kwargs_snap, extra_snap)


def set_model(model: Any) -> None:
    """Attach the live training model to the rank-root state and refresh
    ``model.*`` invariants in the C bridge. Called from the DDP / FSDP /
    DeepSpeed init wrappers — at ``open_rank_span`` time the model is
    still being constructed and a parameter-walk would either fail or
    miss the wrapped submodules.
    """
    if model is None:
        return
    # Build the new extra dict OUTSIDE the lock — a `parameters()` /
    # `named_modules()` walk on a 70B-param FSDP model is non-trivial CPU
    # time and must not block the rotation timer, scheduler republish, or
    # set_framework. Then take the lock only to swap atomically.
    with _lock:
        kwargs_for_build = {**_open_kwargs, "model": model}
    try:
        new_extra = _build_extra(kwargs_for_build)
    except Exception:
        log.debug("pytorch: set_model _build_extra failed", exc_info=True)
        return
    global _cached_extra
    with _lock:
        span = _span
        _open_kwargs["model"] = model
        _cached_extra = new_extra
        kwargs_snap = dict(_open_kwargs)
        extra_snap = dict(_cached_extra)
    if span is None:
        return
    try:
        _publish_to_c(span, kwargs_snap, extra_snap)
    except Exception:
        log.debug("pytorch: set_model republish failed", exc_info=True)


def close() -> None:
    """Finish the per-rank span. Safe to call when no span is open."""
    global _span, _atexit_registered, _rotation_timer, _open_kwargs, _cached_extra
    global _dropped_tokens_n, _dropped_tokens_sum, _dropped_tokens_min, _dropped_tokens_max
    with _lock:
        span = _span
        _span = None
        timer = _rotation_timer
        _rotation_timer = None
        # Clear the cached open-kwargs and bundle so a re-bootstrap in the
        # same process (destroy_process_group → init_process_group, or a
        # late re-`patch()`) doesn't see parent-state. See review #2.
        _open_kwargs = {}
        _cached_extra = {}
        if _atexit_registered:
            try:
                atexit.unregister(close)
            except Exception:  # nosec B110
                pass
            _atexit_registered = False
    with _dropped_lock:
        _dropped_tokens_n = 0
        _dropped_tokens_sum = 0.0
        _dropped_tokens_min = 0.0
        _dropped_tokens_max = 0.0

    if timer is not None:
        timer.cancel()

    if span is None:
        return
    try:
        _tag_ray_run_context(span)
        span.finish()
        # Flush in a daemon thread so close() never stalls the caller
        # (e.g. destroy_process_group). The thread is best-effort; on
        # normal process exit atexit fires close() and the daemon gets
        # a chance to complete before the interpreter shuts down.
        threading.Thread(
            target=lambda: _safe_flush(tracer),
            name="dd-pytorch-rank-root-flush",
            daemon=True,
        ).start()
    except Exception:
        log.exception("pytorch: rank-root span close failed")
    finally:
        _c_tracer.clear_parent_context()


def _safe_flush(_tracer: Any) -> None:
    try:
        _tracer.flush()
    except Exception:
        log.debug("pytorch: tracer.flush during rank-root close raised", exc_info=True)


def _reset_child_state() -> None:
    # Clear inherited state; timer threads do not survive fork.
    global _span, _lock, _atexit_registered, _rotation_timer, _open_kwargs, _cached_extra
    global _dropped_lock, _dropped_tokens_n, _dropped_tokens_sum, _dropped_tokens_min, _dropped_tokens_max
    _span = None
    _lock = Lock()
    _atexit_registered = False
    _rotation_timer = None
    _open_kwargs = {}
    # Drop the parent's cached bundle and rolling aggregates — the child
    # must publish its own from a fresh open_rank_span. See review #2.
    _cached_extra = {}
    _dropped_lock = Lock()
    _dropped_tokens_n = 0
    _dropped_tokens_sum = 0.0
    _dropped_tokens_min = 0.0
    _dropped_tokens_max = 0.0
    # Clear C tracer parent pointer — child must not inherit a dangling span ref.
    try:
        _c_tracer.clear_parent_context()
    except Exception:  # nosec B110
        pass


forksafe.register(_reset_child_state)
