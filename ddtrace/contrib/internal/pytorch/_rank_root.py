"""Per-rank lifetime span for PyTorch distributed training.

Emits one ``pytorch.rank`` span per rank, open for the lifetime of the
distributed process group. Carries ``rank``, ``world_size``, ``framework``,
``training_job.id``, and device tags.

The span is rotated every ``_rotation_interval_s`` seconds (default 600)
so partial data is visible during long runs. Rotated spans carry
``_dd.was_long_running=1``.
"""

import atexit
import os
import threading
from typing import Any
from typing import Optional

from ddtrace import config
from ddtrace import tracer
from ddtrace.contrib.internal.pytorch import _c_tracer
from ddtrace.contrib.internal.pytorch import _device
from ddtrace.contrib.internal.trace_utils import ext_service
from ddtrace.internal.logger import get_logger
from ddtrace.internal.settings import env


log = get_logger(__name__)

_lock = threading.Lock()
_span: Optional[Any] = None
_atexit_registered = False
_rotation_interval_s: int = 600
_rotation_timer: Optional[threading.Timer] = None
_open_kwargs: dict[str, Any] = {}


def _build_span(kwargs: dict[str, Any]) -> Optional[Any]:
    rank = kwargs["rank"]
    world_size = kwargs["world_size"]
    framework = kwargs["framework"]
    training_job_id = kwargs["training_job_id"]
    try:
        _parent = tracer.current_span()
        _parent_ctx = _parent.context if _parent is not None else None
        span = tracer.start_span(
            "pytorch.rank",
            service=ext_service(None, config.pytorch),
            child_of=_parent_ctx,
            activate=False,
        )
    except Exception:
        log.debug("pytorch: failed to open pytorch.rank span", exc_info=True)
        return None
    try:
        span._set_attribute("rank", int(rank))
        span._set_attribute("world_size", int(world_size))
        span.set_tag("framework", framework or "none")
        span.set_tag("component", "pytorch")
        span.set_tag("debug.level", "0")
        if training_job_id:
            span.set_tag("training_job.id", training_job_id)
            span.set_tag("job_id", training_job_id)
        # Force-keep: losing this per-rank anchor to base sampling permanently
        # breaks workload attribution via the span's time range.
        span.set_tag("manual.keep")
        info = _device.get()
        if info is not None:
            span.set_tag("device.id", info.device_id)
            span.set_tag("device.kind", info.kind)
            span.set_tag("host", info.hostname)
            if info.device_index is not None:
                span._set_attribute("device.index", info.device_index)
            if info.gpu_name:
                span.set_tag("device.gpu.name", info.gpu_name)
            if info.gpu_compute_capability:
                span.set_tag("device.gpu.compute_capability", info.gpu_compute_capability)
            if info.gpu_sm_count is not None:
                span._set_attribute("device.gpu.sm_count", info.gpu_sm_count)
            if info.gpu_total_memory_bytes is not None:
                span._set_attribute("device.gpu.total_memory_bytes", info.gpu_total_memory_bytes)
            if info.gpu_driver_version:
                span.set_tag("device.gpu.driver_version", info.gpu_driver_version)

        try:
            import torch  # noqa: PLC0415

            torch_ver = getattr(torch, "__version__", "") or ""
            if torch_ver:
                span.set_tag("torch.version", str(torch_ver))
            cuda_ver = getattr(getattr(torch, "version", None), "cuda", None)
            if cuda_ver:
                span.set_tag("torch.cuda.version", str(cuda_ver))
            hip_ver = getattr(getattr(torch, "version", None), "hip", None)
            if hip_ver:
                span.set_tag("torch.cuda.hip_version", str(hip_ver))
            try:
                nccl_ver = torch.cuda.nccl.version()
                if isinstance(nccl_ver, tuple) and nccl_ver:
                    span.set_tag("torch.cuda.nccl_version", ".".join(str(p) for p in nccl_ver))
            except Exception:  # nosec B110
                pass
            cudnn = getattr(getattr(torch, "backends", None), "cudnn", None)
            if cudnn is not None:
                try:
                    span.set_tag("torch.cudnn.enabled", "true" if bool(cudnn.enabled) else "false")
                except Exception:  # nosec B110
                    pass
                try:
                    span.set_tag("torch.cudnn.benchmark", "true" if bool(cudnn.benchmark) else "false")
                except Exception:  # nosec B110
                    pass
                try:
                    span.set_tag(
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
                    span.set_tag("torch.float32_matmul_precision", str(prec))
            except Exception:  # nosec B110
                pass
            try:
                if torch.backends.mps.is_available():
                    span.set_tag("torch.mps.available", "true")
            except Exception:  # nosec B110
                pass
        except Exception:
            log.debug("pytorch.rank: torch invariants tagging failed", exc_info=True)

        try:
            for envvar, tag in (
                ("NCCL_DEBUG", "nccl.debug"),
                ("NCCL_SOCKET_IFNAME", "nccl.socket_ifname"),
                ("NCCL_IB_DISABLE", "nccl.ib_disable"),
                ("NCCL_P2P_DISABLE", "nccl.p2p_disable"),
                ("NCCL_ALGO", "nccl.algo"),
                ("NCCL_PROTO", "nccl.proto"),
                ("TORCH_NCCL_ASYNC_ERROR_HANDLING", "nccl.async_error_handling"),
                ("CUDA_VISIBLE_DEVICES", "device.cuda.visible_devices"),
                ("MASTER_ADDR", "pytorch.master_addr"),
            ):
                val = env.get(envvar)
                if val:
                    span.set_tag(tag, str(val))
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
                span.set_tag("launcher", launcher)
            backend = _get_cached_backend()
            if backend:
                span.set_tag("torch.distributed.backend", backend)
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
            span.set_tag("ray.train.run_name", rn)
        if sub:
            span.set_tag("ray.submission_id", sub)
        for k, v in md.items():
            try:
                span.set_tag(f"ray.metadata.{k}", str(v))
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

    new_span = _build_span(_open_kwargs)

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
    # ensures no gap in coverage for GPU-level root spans.
    if new_span is not None:
        _c_tracer.set_parent_context(new_span, _open_kwargs)

    try:
        old_span.set_tag("_dd.was_long_running", 1)
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
    global _span, _atexit_registered, _open_kwargs
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

    new_span = _build_span(_open_kwargs)

    won_race = False
    with _lock:
        if _span is None:
            _span = new_span
            won_race = True
            _schedule_rotation()
        else:
            # Lost the race to another concurrent open_rank_span() — discard.
            if new_span is not None:  # type: ignore[unreachable]
                try:
                    new_span.finish()
                except Exception:  # nosec B110
                    pass

    if won_race and new_span is not None:
        _c_tracer.set_parent_context(new_span, _open_kwargs)


def set_framework(name: str) -> None:
    """Update the ``framework`` tag on the open ``pytorch.rank`` span."""
    if not name:
        return
    with _lock:
        span = _span
        _open_kwargs["framework"] = name  # keep rotation in sync
    if span is None:
        return
    try:
        span.set_tag("framework", name)
    except Exception:
        log.debug("pytorch: failed to set framework tag", exc_info=True)
    _c_tracer.set_parent_context(span, _open_kwargs)


def close() -> None:
    """Finish the per-rank span. Safe to call when no span is open."""
    global _span, _atexit_registered, _rotation_timer
    import os as _os, sys as _sys  # noqa: PLC0415
    _verbose = bool(_os.environ.get("DD_CTRACER_BRIDGE_VERBOSE"))
    if _verbose:
        _sys.stderr.write(f"[rank-root] close() entered pid={_os.getpid()}\n"); _sys.stderr.flush()
    with _lock:
        span = _span
        _span = None
        timer = _rotation_timer
        _rotation_timer = None
        if _atexit_registered:
            try:
                atexit.unregister(close)
            except Exception:  # nosec B110
                pass
            _atexit_registered = False

    if timer is not None:
        timer.cancel()

    if span is None:
        if _verbose:
            _sys.stderr.write(f"[rank-root] close() pid={_os.getpid()} no span open\n"); _sys.stderr.flush()
        return
    try:
        _tag_ray_run_context(span)
        span.finish()
        if _verbose:
            _sys.stderr.write(f"[rank-root] close() pid={_os.getpid()} span.finish() done\n"); _sys.stderr.flush()
        flush_thread = threading.Thread(
            target=lambda: _safe_flush(tracer),
            name="dd-pytorch-rank-root-flush",
            daemon=True,
        )
        flush_thread.start()
        flush_thread.join(timeout=2.0)
        if _verbose:
            alive = flush_thread.is_alive()
            _sys.stderr.write(f"[rank-root] close() pid={_os.getpid()} flush joined alive={alive}\n"); _sys.stderr.flush()
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
    global _span, _lock, _atexit_registered, _rotation_timer, _open_kwargs
    _span = None
    _lock = threading.Lock()
    _atexit_registered = False
    _rotation_timer = None
    _open_kwargs = {}


if hasattr(os, "register_at_fork"):
    os.register_at_fork(after_in_child=_reset_child_state)
