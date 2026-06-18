"""ctypes bridge to the dd-trace-c global parent context API.

The C tracer is injected into the process via LD_PRELOAD by the Datadog
injection layer — dd-trace-py does not load it. All public functions here
are silent no-ops when the C tracer is not present.
"""

import ctypes
from typing import Any
from typing import Callable
from typing import Optional

from ddtrace.internal.logger import get_logger


log = get_logger(__name__)

_lib: Optional[ctypes.CDLL] = None
_loaded: bool = False
_set_fn: Optional[Callable[..., None]] = None
_clear_fn: Optional[Callable[[], None]] = None
_step_begin_fn: Optional[Callable[[], None]] = None
_step_end_fn: Optional[Callable[[], None]] = None


def _load() -> bool:
    """Bind to C tracer symbols already in the process namespace. Returns True on success."""
    global _lib, _loaded, _set_fn, _clear_fn, _step_begin_fn, _step_end_fn
    if _loaded:
        return _lib is not None
    _loaded = True

    try:
        # ctypes.CDLL(None) opens the global symbol table, which includes any
        # library injected via LD_PRELOAD — no explicit library loading needed.
        lib = ctypes.CDLL(None)
        fn = lib.dd_set_global_parent_context
        fn.restype = None
        fn.argtypes = [
            ctypes.c_uint64,  # trace_id (low 64 bits)
            ctypes.c_uint64,  # trace_id_hi (high 64 bits)
            ctypes.c_uint64,  # span_id
            ctypes.c_bool,  # has_sampling_priority
            ctypes.c_int,  # sampling_priority
            ctypes.POINTER(ctypes.c_char_p),  # keys
            ctypes.POINTER(ctypes.c_char_p),  # values
            ctypes.c_size_t,  # count
        ]
        _set_fn = fn

        fn2 = lib.dd_clear_global_parent_context
        fn2.restype = None
        fn2.argtypes = []
        _clear_fn = fn2
    except AttributeError:
        # C tracer not present in this process — no-op path.
        return False

    # Step signals — only available in C tracer builds that include training.c.
    # Silently absent means the heuristic NCCL-group-marker fallback activates.
    try:
        fn3 = lib.dd_training_step_begin
        fn3.restype = None
        fn3.argtypes = []
        _step_begin_fn = fn3
        fn4 = lib.dd_training_step_end
        fn4.restype = None
        fn4.argtypes = []
        _step_end_fn = fn4
    except AttributeError:
        pass

    # Counters fed by Python wrappers (AMP scaler, clip_grad_norm_,
    # MoE dropped-tokens). Each entry is independent — missing one
    # symbol just disables that channel.
    try:
        fn5 = lib.dd_training_record_amp_skipped
        fn5.restype = None
        fn5.argtypes = []
        _amp_skipped_fn = fn5
    except AttributeError:
        pass
    try:
        fn6 = lib.dd_training_record_dropped_tokens
        fn6.restype = None
        fn6.argtypes = [ctypes.c_uint64]
        _dropped_tokens_fn = fn6
    except AttributeError:
        pass
    try:
        fn7 = lib.dd_training_record_grad_clip_ns
        fn7.restype = None
        fn7.argtypes = [ctypes.c_uint64]
        _grad_clip_ns_fn = fn7
    except AttributeError:
        pass

    _lib = lib
    return True


def set_parent_context(span: Any, open_kwargs: dict[str, Any]) -> None:
    """Register *span* as the process-wide parent for all C-tracer root spans.

    No-op when the C tracer is not present. Never raises.
    """
    if not _load() or _set_fn is None:
        return
    try:
        trace_id = span.trace_id
        span_id = ctypes.c_uint64(span.span_id)
        trace_id_lo = ctypes.c_uint64(trace_id & 0xFFFFFFFFFFFFFFFF)
        trace_id_hi = ctypes.c_uint64((trace_id >> 64) & 0xFFFFFFFFFFFFFFFF)

        priority = getattr(getattr(span, "context", None), "sampling_priority", None)
        has_priority = ctypes.c_bool(priority is not None)
        c_priority = ctypes.c_int(int(priority) if priority is not None else 0)

        # C API uses underscore-separated keys; Python span tags use dot-separated
        # (e.g. "training_job.id"). These are intentionally different namespaces.
        tags = {
            "training_job_id": str(open_kwargs.get("training_job_id") or ""),
            "rank": str(open_kwargs.get("rank", 0)),
            "world_size": str(open_kwargs.get("world_size", 1)),
            "framework": str(open_kwargs.get("framework") or "none"),
            "service": str(getattr(span, "service", None) or ""),
        }
        # Merge any extra stringifiable keys (from _c_bridge.build_init_bundle).
        # Skip the raw nn.Module under "model" and any private (_-prefixed) kwargs.
        for k, v in open_kwargs.items():
            if k in tags or k.startswith("_") or k == "model":
                continue
            try:
                sv = str(v) if v is not None else ""
            except Exception:
                continue
            if sv and len(k) < 64 and len(sv) < 256:
                tags[k] = sv

        # Hard cap at 64 — C bridge silently truncates, but enforce here so
        # the bundle's contents stay deterministic.
        if len(tags) > 64:
            tags = dict(list(tags.items())[:64])

        keys_enc = [k.encode() for k in tags]
        vals_enc = [v.encode() for v in tags.values()]
        ArrType = ctypes.c_char_p * len(tags)

        _set_fn(
            trace_id_lo,
            trace_id_hi,
            span_id,
            has_priority,
            c_priority,
            ArrType(*keys_enc),
            ArrType(*vals_enc),
            ctypes.c_size_t(len(tags)),
        )
    except Exception:
        log.debug("pytorch: dd_set_global_parent_context failed", exc_info=True)


def clear_parent_context() -> None:
    """Clear the process-wide parent context. No-op when C tracer is absent. Never raises."""
    if not _load() or _clear_fn is None:
        return
    try:
        _clear_fn()
    except Exception:
        log.debug("pytorch: dd_clear_global_parent_context failed", exc_info=True)


def step_begin() -> None:
    """Signal start of a training step (forward pass begins). No-op when C tracer absent. Never raises."""
    if not _load() or _step_begin_fn is None:
        return
    try:
        _step_begin_fn()
    except Exception:
        log.debug("pytorch: dd_training_step_begin failed", exc_info=True)


def step_end() -> None:
    """Signal end of a training step (optimizer step complete). No-op when C tracer absent. Never raises."""
    if not _load() or _step_end_fn is None:
        return
    try:
        _step_end_fn()
    except Exception:
        log.debug("pytorch: dd_training_step_end failed", exc_info=True)


def record_amp_skipped() -> None:
    """Bump the AMP grad-scaler skipped-step counter. No-op when absent."""
    if not _load() or _amp_skipped_fn is None:
        return
    try:
        _amp_skipped_fn()
    except Exception:
        log.debug("pytorch: dd_training_record_amp_skipped failed", exc_info=True)


def record_dropped_tokens(n: int) -> None:
    """Record one MoE dropped-token observation."""
    if not _load() or _dropped_tokens_fn is None:
        return
    try:
        _dropped_tokens_fn(ctypes.c_uint64(int(n)))
    except Exception:
        log.debug("pytorch: dd_training_record_dropped_tokens failed", exc_info=True)


def record_grad_clip_ns(ns: int) -> None:
    """Record one clip_grad_norm_ wall-time observation."""
    if not _load() or _grad_clip_ns_fn is None:
        return
    try:
        _grad_clip_ns_fn(ctypes.c_uint64(int(ns)))
    except Exception:
        log.debug("pytorch: dd_training_record_grad_clip_ns failed", exc_info=True)
