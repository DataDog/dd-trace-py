"""Device-id discovery for Layer Zero metric tagging.

AIDEV-NOTE: Device id is intentionally a fleet-stable dimension (GPU UUID,
not rank). Job-relative attribution (training_job.id, rank) lives on the
rank-root span — operators correlate metrics to a job via the span's time
range, not via metric tags. This keeps custom-metric cardinality bounded
by physical fleet size regardless of how many jobs run on the fleet.
"""

import socket
import threading
from typing import NamedTuple
from typing import Optional

from ddtrace.internal.settings import env


class DeviceInfo(NamedTuple):
    device_id: str
    device_index: Optional[int]
    kind: str  # "cuda" | "cpu"
    hostname: str
    # New fields — defensive Optional[...] because older torch versions may
    # not expose all of them, and CPU-only hosts return None for all.
    gpu_name: Optional[str] = None
    gpu_compute_capability: Optional[str] = None  # e.g. "8.0"
    gpu_sm_count: Optional[int] = None
    gpu_total_memory_bytes: Optional[int] = None
    gpu_driver_version: Optional[str] = None


_cache: Optional[DeviceInfo] = None
_lock = threading.Lock()


def _cuda_is_available() -> bool:
    try:
        import torch

        return bool(torch.cuda.is_available())
    except Exception:
        return False


def _cuda_index(local_rank: int) -> Optional[int]:
    # Bootstrap fires inside the `init_process_group` wrap, BEFORE
    # `torch.cuda.set_device(local_rank)` has run. At that point
    # `torch.cuda.current_device()` returns 0 for every rank, so
    # multiple ranks on the same host end up with the same (wrong)
    # device.index and UUID. Resolve in priority order:
    #   1. `LOCAL_RANK` env var (torchrun, DeepSpeed, MPI launchers).
    #   2. `ray.train.get_context().get_local_rank()` (Ray Train v2
    #      doesn't export LOCAL_RANK to env, only via its API).
    #   3. `torch.cuda.current_device()` — last-resort fallback for
    #      single-process / non-distributed runs.
    try:
        env_local = env.get("LOCAL_RANK")
        if env_local is not None and env_local != "":
            return int(env_local)
    except Exception:
        pass
    try:
        import ray.train  # type: ignore[import-not-found]

        ctx = ray.train.get_context()
        return int(ctx.get_local_rank())
    except Exception:
        pass
    try:
        import torch

        return int(torch.cuda.current_device())
    except Exception:
        return None


def _query_cuda_uuid(idx: int) -> Optional[str]:
    # AIDEV-NOTE: `idx` must equal the recorded device_index so UUID and
    # index refer to the same physical GPU. Prefer pynvml (stable across
    # torch versions); fall back to torch device-properties UUID (2.0+).
    try:
        import pynvml  # type: ignore[import-not-found]

        pynvml.nvmlInit()
        try:
            handle = pynvml.nvmlDeviceGetHandleByIndex(idx)
            raw = pynvml.nvmlDeviceGetUUID(handle)
            return raw.decode() if isinstance(raw, bytes) else str(raw)
        finally:
            try:
                pynvml.nvmlShutdown()
            except Exception:
                pass
    except Exception:
        pass
    try:
        import torch

        props = torch.cuda.get_device_properties(idx)
        uuid = getattr(props, "uuid", None)
        if uuid is not None:
            return str(uuid)
    except Exception:
        pass
    return None


def _query_cuda_props(idx: int) -> dict:
    """Best-effort fetch of additional device fields from
    `torch.cuda.get_device_properties(idx)`. Returns a dict with only the
    fields we managed to read; missing fields are omitted.
    """
    out: dict = {}
    try:
        import torch  # noqa: PLC0415

        props = torch.cuda.get_device_properties(idx)
    except Exception:
        return out
    name = getattr(props, "name", None)
    if name:
        out["gpu_name"] = str(name)
    major = getattr(props, "major", None)
    minor = getattr(props, "minor", None)
    if major is not None and minor is not None:
        out["gpu_compute_capability"] = f"{int(major)}.{int(minor)}"
    sm = getattr(props, "multi_processor_count", None)
    if sm is not None:
        try:
            out["gpu_sm_count"] = int(sm)
        except Exception:
            pass
    total = getattr(props, "total_memory", None)
    if total is not None:
        try:
            out["gpu_total_memory_bytes"] = int(total)
        except Exception:
            pass
    return out


def _query_cuda_driver_version() -> Optional[str]:
    try:
        import pynvml  # type: ignore[import-not-found]  # noqa: PLC0415

        pynvml.nvmlInit()
        try:
            raw = pynvml.nvmlSystemGetDriverVersion()
            return raw.decode() if isinstance(raw, bytes) else str(raw)
        finally:
            try:
                pynvml.nvmlShutdown()
            except Exception:
                pass
    except Exception:
        return None


def _hostname() -> str:
    try:
        return socket.gethostname()
    except Exception:
        return "unknown-host"


def discover(local_rank: int) -> DeviceInfo:
    """Resolve and cache the device id. Idempotent — second call returns the cached value."""
    global _cache
    with _lock:
        if _cache is not None:
            return _cache
        host = _hostname()
        if _cuda_is_available():
            idx = _cuda_index(local_rank)
            if idx is None:
                # AIDEV-NOTE: When `torch.cuda.current_device()` raised
                # we can't reliably map local_rank → physical device.
                # Emit a `device_index=None` info and skip the UUID
                # lookup (the index passed in would be a guess).
                _cache = DeviceInfo(
                    device_id="%s:cuda:unknown" % host,
                    device_index=None,
                    kind="cuda",
                    hostname=host,
                )
                return _cache
            uuid = _query_cuda_uuid(idx)
            device_id = uuid if uuid else "%s:cuda:%d" % (host, idx)
            props = _query_cuda_props(idx)
            driver_v = _query_cuda_driver_version()
            _cache = DeviceInfo(
                device_id=device_id,
                device_index=idx,
                kind="cuda",
                hostname=host,
                gpu_name=props.get("gpu_name"),
                gpu_compute_capability=props.get("gpu_compute_capability"),
                gpu_sm_count=props.get("gpu_sm_count"),
                gpu_total_memory_bytes=props.get("gpu_total_memory_bytes"),
                gpu_driver_version=driver_v,
            )
        else:
            # AIDEV-NOTE: drops local_rank — CPU is one logical device per host for
            # cardinality bounding; per-process distinction comes from the rank-root
            # span tags.
            _cache = DeviceInfo(
                device_id="%s:cpu" % host,
                device_index=None,
                kind="cpu",
                hostname=host,
            )
        return _cache


def get() -> Optional[DeviceInfo]:
    """Return the cached DeviceInfo, or None if `discover` has not yet run."""
    return _cache


# Per-GPU peak FLOPs by dtype, in FLOPS (not TFLOPS).
# Maintenance: add new GPUs here as needed. Values from official datasheets.
_PEAK_FLOPS_TABLE: dict = {
    # NVIDIA H100 SXM5 / PCIe — figures for tensor cores
    ("H100", "bfloat16"): 989e12,
    ("H100", "float16"): 989e12,
    ("H100", "tf32"): 495e12,
    ("H100", "float32"): 67e12,
    # NVIDIA A100 SXM4 / PCIe
    ("A100", "bfloat16"): 312e12,
    ("A100", "float16"): 312e12,
    ("A100", "tf32"): 156e12,
    ("A100", "float32"): 19.5e12,
    # NVIDIA L40 / L4 — Ada Lovelace. fp16 shares bf16 tensor-core path;
    # fp32 is the non-tensor ALU peak per datasheet.
    ("L40", "bfloat16"): 181e12,
    ("L40", "float16"): 181e12,
    ("L40", "float32"): 90.5e12,
    ("L4", "bfloat16"): 121e12,
    ("L4", "float16"): 121e12,
    ("L4", "float32"): 30.3e12,
    # NVIDIA V100
    ("V100", "float16"): 125e12,
    ("V100", "float32"): 15.7e12,
    # NVIDIA T4
    ("T4", "float16"): 65e12,
    ("T4", "float32"): 8.1e12,
    # AMD MI300X — CDNA3 matrix peaks per datasheet; fp32 is the vector ALU peak.
    ("MI300", "bfloat16"): 1300e12,
    ("MI300", "float16"): 1300e12,
    ("MI300", "float32"): 163.4e12,
}


def lookup_peak_flops(gpu_name: Optional[str], dtype: str) -> Optional[float]:
    """Best-effort lookup: substring-match `gpu_name` against table prefixes.
    Returns None if no match.
    """
    if not gpu_name:
        return None
    for (prefix, dt), v in _PEAK_FLOPS_TABLE.items():
        if dt == dtype and prefix in gpu_name:
            return v
    return None
