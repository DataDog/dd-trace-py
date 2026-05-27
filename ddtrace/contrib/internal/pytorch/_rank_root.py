"""Per-rank lifetime span for Layer Zero.

One ``pytorch.rank`` span per rank. Opened at distributed bootstrap
(``init_process_group``) and finished on the first of:

- ``torch.distributed.destroy_process_group`` (canonical graceful
  teardown, wrapped from ``_distributed.py``),
- explicit ``unpatch()``,
- process exit via ``atexit``.

Carries the rank-context tags (``training_job.id``, ``rank``,
``world_size``, ``framework``, ``device.id``) so metric queries correlate
to a specific job via the span's time range, plus per-op collective
duration percentiles emitted as numeric facets (``collective.<op>.p99_ms``
etc.) for straggler diagnosis.

AIDEV-NOTE: Nests under an active ``ray.train.worker`` span when present.

AIDEV-NOTE: ``open()`` registers ``close`` with ``atexit`` because the
common deployment path (``ddtrace-run python train.py``) never calls
``unpatch()``. ``SIGKILL`` / ``os._exit()`` bypass both atexit *and* the
``destroy_process_group`` hook; on those paths the rank span is lost.
There is no periodic flush — the design accepts that hard-killed ranks
are unreported.
"""

import atexit
import os
import threading

from ddtrace import config
from ddtrace import tracer
from ddtrace.contrib.internal.pytorch import _device
from ddtrace.contrib.internal.pytorch import _metrics
from ddtrace.internal.logger import get_logger
from ddtrace.internal.settings import env


log = get_logger(__name__)

_lock = threading.Lock()
_span = None  # currently-open pytorch.rank span (or None)
_atexit_registered = False


def _build_span(kwargs: dict):
    """Create and tag a new pytorch.rank span from the given kwargs dict."""
    rank = kwargs["rank"]
    world_size = kwargs["world_size"]
    framework = kwargs["framework"]
    training_job_id = kwargs["training_job_id"]
    try:
        span = tracer.start_span(
            "pytorch.rank",
            service=config.pytorch.service,
            child_of=tracer.current_span(),
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
        # Force-keep the trace: pytorch.rank is the per-run anchor that
        # ties metrics (device-tagged, no training_job.id) back to a job
        # via its time range. Losing this span to base sampling
        # permanently breaks workload attribution.
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

        # Torch / CUDA / cuDNN / NCCL invariants (one-shot at span open).
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
            except Exception:
                pass
            cudnn = getattr(getattr(torch, "backends", None), "cudnn", None)
            if cudnn is not None:
                try:
                    span.set_tag("torch.cudnn.enabled", "true" if bool(cudnn.enabled) else "false")
                except Exception:
                    pass
                try:
                    span.set_tag("torch.cudnn.benchmark", "true" if bool(cudnn.benchmark) else "false")
                except Exception:
                    pass
                try:
                    span.set_tag(
                        "torch.cudnn.deterministic",
                        "true" if bool(cudnn.deterministic) else "false",
                    )
                except Exception:
                    pass
                try:
                    v = cudnn.version()
                    if isinstance(v, int):
                        span._set_attribute("torch.cudnn.version", v)
                except Exception:
                    pass
            try:
                prec = torch.get_float32_matmul_precision()
                if prec:
                    span.set_tag("torch.float32_matmul_precision", str(prec))
            except Exception:
                pass
            try:
                if torch.backends.mps.is_available():
                    span.set_tag("torch.mps.available", "true")
            except Exception:
                pass
        except Exception:
            log.debug("pytorch.rank: torch invariants tagging failed", exc_info=True)

        # NCCL / CUDA / torchrun env signals (string tags + int facets).
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
                    except Exception:
                        pass
        except Exception:
            log.debug("pytorch.rank: env-signal tagging failed", exc_info=True)

        # Launcher + distributed backend (both helpers cache their results).
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


def _tag_ray_run_context(span) -> None:
    """Stamp Ray Train run context (``ray.submission_id``,
    ``ray.train.run_name``, ``ray.metadata.*``) onto ``span`` from the
    pytorch-utils cache. Best-effort and idempotent — safe to call at
    both open and close.

    AIDEV-NOTE: Called at close as well as open because the Ray worker
    wrapper that populates the cache (``_run_train_func_in_worker``)
    runs *after* Ray Train has already invoked
    ``init_process_group`` → ``_rank_root.open``. By close time the
    cache is reliably populated and we can backfill the rank span.
    """
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


def _tag_collective_summary(span) -> None:
    """Drain the per-op duration reservoirs and attach percentiles to `span`.

    AIDEV-NOTE: MUST be called before `span.finish()` — metric mutations on
    a finished span are dropped silently.

    Emits each percentile as a flat numeric facet so the spans aggregate
    API can compute per-rank/per-run straggler verdicts server-side
    instead of forcing clients to download raw spans and JSON-parse a
    summary blob. Layout:

        collective.<op>.p10_ms, .p50_ms, .p90_ms, .p99_ms    — per op
        collective.<op>.n                                      — per op sample count
        collective.p99_max_ms                                  — max p99 across all ops on this rank
        collective.ops_count                                   — number of ops with samples

    `<op>` is sanitized to keep facet names well-formed (dots replaced
    with underscores). Reservoirs are in milliseconds throughout.
    """
    try:
        summary = _metrics.summary_snapshot_and_reset()
    except Exception:
        log.debug("pytorch: failed to snapshot collective summary", exc_info=True)
        summary = None
    if summary:
        try:
            max_p99 = 0.0
            for op, stats in summary.items():
                op_safe = op.replace(".", "_")
                for pct in ("p10", "p50", "p90", "p99"):
                    v = stats.get(pct)
                    if isinstance(v, (int, float)):
                        span._set_attribute(f"collective.{op_safe}.{pct}_ms", float(v))
                n = stats.get("n")
                if isinstance(n, int):
                    span._set_attribute(f"collective.{op_safe}.n", n)
                p99 = stats.get("p99")
                if isinstance(p99, (int, float)) and p99 > max_p99:
                    max_p99 = float(p99)
            if max_p99 > 0:
                span._set_attribute("collective.p99_max_ms", max_p99)
            span._set_attribute("collective.ops_count", len(summary))
        except Exception:
            log.debug("pytorch: failed to set collective.* numeric facets", exc_info=True)

    # NEW: drain training-metric reservoirs from _summary and stamp on span.
    try:
        from ddtrace.contrib.internal.pytorch import _summary  # noqa: PLC0415

        facets = _summary.drain_all_to_facets()
        for k, v in facets.items():
            try:
                span._set_attribute(k, v)
            except Exception:
                log.debug("pytorch.rank: failed to set summary facet %s", k, exc_info=True)
    except Exception:
        log.debug("pytorch.rank: failed to drain training-metric reservoirs", exc_info=True)


def open(rank: int, world_size: int, framework: str, training_job_id):  # noqa: A001
    """Open the per-rank lifetime span. Idempotent — second call is a no-op."""
    global _span, _atexit_registered
    with _lock:
        if _span is not None:
            return
        if not _atexit_registered:
            atexit.register(close)
            _atexit_registered = True
        _span = _build_span(
            {
                "rank": rank,
                "world_size": world_size,
                "framework": framework,
                "training_job_id": training_job_id,
            }
        )


def set_framework(name: str) -> None:
    """Update the `framework` tag on the open `pytorch.rank` span.

    AIDEV-NOTE: Called from DDP/FSDP/DeepSpeed ``__init__`` wrappers; the
    rank-root span opens at ``init_process_group`` time, before any
    framework wrapper has fired. Safe no-op when no rank-root is open.
    """
    if not name:
        return
    with _lock:
        span = _span
    if span is None:
        return
    try:
        span.set_tag("framework", name)
    except Exception:
        log.debug("pytorch: failed to set framework tag on rank-root", exc_info=True)


def close() -> None:
    """Finish the per-rank span. Safe to call when no span is open."""
    global _span, _atexit_registered
    with _lock:
        span = _span
        _span = None
        # Unregister so a subsequent open()/close() cycle doesn't accumulate
        # atexit callbacks (one per cycle) across long-running test sessions.
        if _atexit_registered:
            try:
                atexit.unregister(close)
            except Exception:
                pass
            _atexit_registered = False
    if span is None:
        return
    try:
        _tag_ray_run_context(span)
        _tag_collective_summary(span)
        span.finish()
        flush_thread = threading.Thread(
            target=lambda: _safe_flush(tracer),
            name="dd-pytorch-rank-root-flush",
            daemon=True,
        )
        flush_thread.start()
        flush_thread.join(timeout=1.0)
        if flush_thread.is_alive():
            log.warning("pytorch: tracer.flush at rank-root close exceeded 1s budget; continuing exit")
    except Exception:
        log.debug("pytorch: failed to finish pytorch.rank span", exc_info=True)


def _safe_flush(_tracer) -> None:
    try:
        _tracer.flush()
    except Exception:
        log.debug("pytorch: tracer.flush during rank-root close raised", exc_info=True)


def _reset_child_state() -> None:
    # AIDEV-NOTE: Drop the inherited span (it belongs to the parent's
    # trace). The child can re-bootstrap if it calls open().
    global _span, _lock, _atexit_registered
    _span = None
    _lock = threading.Lock()
    _atexit_registered = False


if hasattr(os, "register_at_fork"):
    os.register_at_fork(after_in_child=_reset_child_state)
