"""Builds the dict of torch / cuDNN / distributed / Ray / model
invariants the C tracer can't read on its own. Merged into the
``_open_kwargs`` payload passed through ``_c_tracer.set_parent_context``.

A DeepSpeedEngine input is unwrapped to its inner ``nn.Module`` for
the model walk and tags ``framework=deepspeed`` — unless the upstream
caller already set ``framework`` (DeepSpeed often wraps a DDP module).
"""

from __future__ import annotations

import os
from typing import Any
from typing import Optional

from ddtrace.internal.logger import get_logger


log = get_logger(__name__)

_MAX_KEYS = 64  # matches dd-trace-c's DD_PARENT_CONTEXT_MAX_TAGS


def _import_torch():
    import torch  # type: ignore

    return torch


def _str_bool(b: Any) -> str:
    return "true" if bool(b) else "false"


def _safe(callable_, *args, default=None):
    try:
        return callable_(*args)
    except Exception:
        return default


def _add_torch_invariants(bundle: dict) -> None:
    try:
        torch = _import_torch()
    except Exception:
        log.debug("pytorch._c_bridge: torch unavailable", exc_info=True)
        return

    v = _safe(lambda: torch.version.__version__)
    if v:
        bundle["torch.version"] = str(v)

    if _safe(lambda: torch.cuda.is_available(), default=False):
        cv = _safe(lambda: torch.version.cuda)
        if cv:
            bundle["torch.cuda.version"] = str(cv)

    cudnn = _safe(lambda: torch.backends.cudnn)
    if cudnn is not None:
        avail = _safe(lambda: cudnn.is_available(), default=False)
        bundle["torch.cudnn.enabled"] = _str_bool(_safe(lambda: cudnn.enabled, default=False) if avail else False)
        bundle["torch.cudnn.benchmark"] = _str_bool(_safe(lambda: cudnn.benchmark, default=False))
        bundle["torch.cudnn.deterministic"] = _str_bool(_safe(lambda: cudnn.deterministic, default=False))
        ver = _safe(lambda: cudnn.version())
        if ver:
            bundle["torch.cudnn.version"] = str(ver)

    prec = _safe(lambda: torch.get_float32_matmul_precision())
    if prec:
        bundle["torch.float32_matmul_precision"] = str(prec)

    mps_avail = _safe(lambda: torch.backends.mps.is_available(), default=False)
    bundle["torch.mps.available"] = _str_bool(mps_avail)

    if _safe(lambda: torch.distributed.is_initialized(), default=False):
        be = _safe(lambda: torch.distributed.get_backend())
        if be:
            bundle["torch.distributed.backend"] = str(be)


def _add_operator_overrides(bundle: dict) -> None:
    """Operator-set knobs the C tracer consumes but can't derive."""
    peak = os.environ.get("DD_PYTORCH_MFU_PEAK_TFLOPS")
    if peak:
        bundle["mfu.peak_tflops"] = str(peak)
    thr = os.environ.get("DD_PYTORCH_STEP_BOUNDARY_THRESHOLD_MS")
    if thr:
        bundle["step_boundary_threshold_ms"] = str(thr)


def _add_ray_metadata(
    bundle: dict,
    ray_run_name: Optional[str],
    ray_submission_id: Optional[str],
) -> None:
    if ray_run_name:
        bundle["ray.train.run_name"] = str(ray_run_name)
    if ray_submission_id:
        bundle["ray.submission_id"] = str(ray_submission_id)
    env_jid = os.environ.get("RAY_JOB_ID")
    if env_jid and "ray.submission_id" not in bundle:
        bundle["ray.submission_id"] = env_jid


def _unwrap_deepspeed(model: Any):
    """Returns ``(inner_module, is_deepspeed)``. Recognises a class named
    ``DeepSpeedEngine`` or any class living under the ``deepspeed.*``
    package that exposes a ``.module`` attribute."""
    if model is None:
        return None, False
    try:
        cls = type(model)
        cls_name = getattr(cls, "__name__", "")
        cls_module = getattr(cls, "__module__", "") or ""
        looks_like_ds = cls_name == "DeepSpeedEngine" or cls_module.startswith("deepspeed.")
        if looks_like_ds and hasattr(model, "module"):
            return model.module, True
    except Exception:
        log.debug("pytorch._c_bridge: DeepSpeed unwrap probe failed", exc_info=True)
    return model, False


_TRANSFORMER_CLASSES = frozenset(
    {
        "TransformerEncoderLayer",
        "TransformerDecoderLayer",
        "TransformerEncoder",
        "TransformerDecoder",
        "MultiheadAttention",
    }
)


def _detect_transformer(module: Any) -> bool:
    try:
        for sub in module.modules():
            if type(sub).__name__ in _TRANSFORMER_CLASSES:
                return True
    except Exception:
        pass
    return False


def _detect_hidden_dim(module: Any) -> Optional[int]:
    # HuggingFace-style configs expose this directly.
    for attr_path in (("config", "hidden_size"), ("config", "d_model"), ("config", "n_embd")):
        try:
            obj = module
            for attr in attr_path:
                obj = getattr(obj, attr)
            if isinstance(obj, int) and obj > 0:
                return obj
        except Exception:
            pass
    # Fall back to the first Linear/Embedding output dim.
    try:
        for sub in module.modules():
            n = type(sub).__name__
            if n == "Linear" and isinstance(getattr(sub, "out_features", None), int):
                return int(sub.out_features)
            if n == "Embedding" and isinstance(getattr(sub, "embedding_dim", None), int):
                return int(sub.embedding_dim)
    except Exception:
        pass
    return None


def _add_model_invariants(bundle: dict, model: Any, framework_already_set: bool) -> None:
    inner, is_deepspeed = _unwrap_deepspeed(model)
    # Don't overwrite an upstream framework value; DeepSpeed often
    # wraps a DDP module, and `ddp` is more informative than `deepspeed`.
    if is_deepspeed and not framework_already_set:
        bundle["framework"] = "deepspeed"
    if inner is None:
        return
    try:
        total = 0
        trainable = 0
        dtype = None
        for p in inner.parameters():
            n = int(p.numel())
            total += n
            if getattr(p, "requires_grad", False):
                trainable += n
            if dtype is None:
                dtype = str(getattr(p, "dtype", "")) or None
        bundle["model.param_count"] = str(total)
        bundle["model.trainable_param_count"] = str(trainable)
        # For dense models active == trainable; MoE-aware count would
        # require model-specific introspection we don't attempt here.
        bundle["model.active_param_count"] = str(trainable or total)
        if dtype:
            bundle["model.dtype"] = dtype
        layers = sum(1 for _ in inner.modules())
        if layers > 0:
            bundle["model.layers"] = str(layers)
        bundle["model.is_transformer"] = "true" if _detect_transformer(inner) else "false"
        hidden = _detect_hidden_dim(inner)
        if hidden is not None:
            bundle["model.hidden_dim"] = str(hidden)
    except Exception:
        log.debug("pytorch._c_bridge: model invariants extraction failed", exc_info=True)


def build_init_bundle(
    model: Any = None,
    ray_run_name: Optional[str] = None,
    ray_submission_id: Optional[str] = None,
    framework_already_set: bool = False,
) -> dict:
    """Build the init bundle. Never raises. Values are stringified;
    the C bridge accepts char* only."""
    bundle: dict = {}
    _add_torch_invariants(bundle)
    _add_ray_metadata(bundle, ray_run_name, ray_submission_id)
    _add_model_invariants(bundle, model, framework_already_set)
    _add_operator_overrides(bundle)

    # Leave room for the 5 required keys the caller merges in
    # (training_job_id, rank, world_size, framework, service).
    if len(bundle) > _MAX_KEYS - 5:
        log.debug("pytorch._c_bridge: bundle truncated, keys=%d", len(bundle))
        bundle = dict(sorted(bundle.items())[: _MAX_KEYS - 5])
    return bundle
