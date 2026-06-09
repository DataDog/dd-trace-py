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


def _load() -> bool:
    """Bind to C tracer symbols already in the process namespace. Returns True on success."""
    global _lib, _loaded, _set_fn, _clear_fn
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
        span_id_val = int(span.span_id)
        span_id = ctypes.c_uint64(span_id_val)
        # Experiments-only verification: print once so we can confirm in Ray
        # actor logs that the bridge actually fires under D config.
        import os as _os, sys as _sys  # noqa: PLC0415
        if _os.environ.get("DD_CTRACER_BRIDGE_VERBOSE"):
            try:
                _sys.stderr.write(
                    f"[dd-trace-py->c] set_parent_context fired pid={_os.getpid()} "
                    f"trace_id={trace_id:032x} span_id={span_id_val:016x}\n"
                )
                _sys.stderr.flush()
            except Exception:  # nosec B110
                pass
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
        }
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
