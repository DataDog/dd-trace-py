"""In-process reservoirs for Layer Zero summary-mode training metrics.

Three reservoir flavors:

- **Distribution** — Algorithm-R reservoir sampling (capped at
  `_RESERVOIR_MAX`). Drains to ``{p10,p50,p90,p99,min,max,mean,count}``.
  Used for step/forward/backward/optim/grad_clip durations, loss, MFU.

- **Gauge** — tracks first / last / min / max / mean over the window.
  No samples retained. Used for LR (where the value trajectory matters)
  and GPU memory.

- **Counter** — simple monotonic bump. Drains to a single integer.
  Used for collective failure tallies.

All reservoirs share `_lock`; reads / writes are lock-protected. The
drain pass is called from `_rank_root.close()` at process exit
(destroy_process_group / unpatch / atexit). The planned rotation ticker
was not implemented; reservoirs accumulate samples over the entire
training run and drain once at process exit. The drain cost is bounded
by the reservoir count, not by the step count.

AIDEV-NOTE: This module is intentionally separate from `_metrics.py`.
`_metrics.py` already exposes its own collective-specific reservoirs;
keeping training-metric reservoirs in `_summary.py` avoids coupling the
collective-rate-emit path to step-boundary timing.
"""

import os
import random
import threading
from typing import Any
from typing import Optional


_RESERVOIR_MAX = 1024
_lock = threading.Lock()

# ---------------------------------------------------------------------------
# Model fingerprinting cache (one-shot per process).
# ---------------------------------------------------------------------------

_model_fingerprinted: bool = False
_model_param_count: int = 0
_model_trainable_param_count: int = 0
_model_active_param_count: int = 0  # equals _model_param_count for non-MoE; adjusted for MoE
_model_layers: int = 0
_model_hidden_dim: int = 0
_model_is_transformer: bool = False
_model_dtype: str = "bfloat16"  # default assumption

_embedding_hook_attached: bool = False
_last_seq_len: int = 0  # most recently captured sequence length from Embedding forward hook
_mfu_oversized_warned: bool = False  # one-time warning when MFU > 1.5

# ---------------------------------------------------------------------------
# MoE detection cache (one-shot at first forward).
# ---------------------------------------------------------------------------

_moe_modules_cached: Optional[list] = None
# Set of attribute names known to expose per-step token counts across
# DeepSpeed-MoE / Megatron-MoE / fairscale.
_MOE_CLASS_NAMES = frozenset(
    (
        "MoE",
        "MOELayer",
        "TopKMoE",
        "TopKGating",
        "SwitchTransformerLayer",
        "MoELayer",
    )
)
_MOE_ROUTED_ATTRS = ("exp_counts", "expert_counts", "tokens_per_expert")
_MOE_TOTAL_ATTRS = ("input_token_count", "n_tokens", "tokens_seen")

# {metric_name: [observed_count, samples_list]}
_dist: dict[str, list[Any]] = {}

# {metric_name: {first, last, sum, count, min, max}}
_gauge: dict[str, dict[str, float]] = {}

# {metric_name: int}
_counter: dict[str, int] = {}


def push_distribution(name: str, value: float) -> None:
    """Algorithm-R reservoir sample for a duration / numeric distribution."""
    with _lock:
        slot = _dist.get(name)
        if slot is None:
            _dist[name] = [1, [float(value)]]
            return
        slot[0] += 1
        buf = slot[1]
        if len(buf) < _RESERVOIR_MAX:
            buf.append(float(value))
            return
        j = random.randrange(slot[0])
        if j < _RESERVOIR_MAX:
            buf[j] = float(value)


def push_gauge(name: str, value: float) -> None:
    """Record a gauge sample (LR, memory).

    Tracks first / last / min / max / running mean. No full sample list.
    """
    v = float(value)
    with _lock:
        slot = _gauge.get(name)
        if slot is None:
            _gauge[name] = {
                "first": v,
                "last": v,
                "sum": v,
                "count": 1,
                "min": v,
                "max": v,
            }
            return
        slot["last"] = v
        slot["sum"] += v
        slot["count"] += 1
        if v < slot["min"]:
            slot["min"] = v
        if v > slot["max"]:
            slot["max"] = v


def bump_counter(name: str, delta: int = 1) -> None:
    with _lock:
        _counter[name] = _counter.get(name, 0) + int(delta)


def _percentile(samples: list[float], p: float) -> float:
    n = len(samples)
    if n == 0:
        return 0.0
    idx = int(p * n)
    if idx >= n:
        idx = n - 1
    return samples[idx]


def drain_distribution(name: str) -> dict[str, float]:
    """Snapshot + reset for a single distribution. Returns {} if empty."""
    with _lock:
        slot = _dist.pop(name, None)
    if slot is None:
        return {"count": 0}
    observed, buf = slot
    if not buf:
        return {"count": observed}
    buf.sort()
    return {
        "count": observed,
        "min": buf[0],
        "max": buf[-1],
        "mean": sum(buf) / len(buf),
        "p10": _percentile(buf, 0.10),
        "p50": _percentile(buf, 0.50),
        "p90": _percentile(buf, 0.90),
        "p99": _percentile(buf, 0.99),
    }


def drain_gauge(name: str) -> dict[str, float]:
    with _lock:
        slot = _gauge.pop(name, None)
    if slot is None:
        return {"count": 0}
    cnt = slot["count"]
    return {
        "first": slot["first"],
        "last": slot["last"],
        "min": slot["min"],
        "max": slot["max"],
        "mean": slot["sum"] / cnt if cnt else 0.0,
        "count": cnt,
    }


def drain_counter(name: str) -> int:
    with _lock:
        return _counter.pop(name, 0)


# AIDEV-NOTE: facet-key suffix conventions match the existing collective
# summary pattern (collective.<op>.p99_ms). Distribution percentiles get
# the `_ms` suffix when the metric is a duration; otherwise plain.
_DURATION_METRICS = frozenset(
    (
        "step.duration_ms",
        "step.forward_ms",
        "step.backward_ms",
        "step.data_fetch_ms",
        "step.optim_step_ms",
        "step.grad_clip_ms",
        "collective.allreduce.gpu_duration_ms",
        "collective.allgather.gpu_duration_ms",
        "collective.broadcast.gpu_duration_ms",
        "collective.reducescatter.gpu_duration_ms",
        "collective.barrier.gpu_duration_ms",
        "grad_comm.bucket_duration_ms",
    )
)


def _emit_distribution_facets(name: str, snap: dict[str, float], out: dict[str, float]) -> None:
    if snap["count"] == 0:
        return
    suffix = "_ms" if name in _DURATION_METRICS else ""
    out[f"{name}.count"] = snap["count"]
    out[f"{name}.min{suffix}"] = snap["min"]
    out[f"{name}.max{suffix}"] = snap["max"]
    out[f"{name}.mean{suffix}"] = snap["mean"]
    out[f"{name}.p10{suffix}"] = snap["p10"]
    out[f"{name}.p50{suffix}"] = snap["p50"]
    out[f"{name}.p90{suffix}"] = snap["p90"]
    out[f"{name}.p99{suffix}"] = snap["p99"]


def _emit_gauge_facets(name: str, snap: dict[str, float], out: dict[str, float]) -> None:
    if snap.get("count", 0) == 0:
        return
    out[f"{name}.first"] = snap["first"]
    out[f"{name}.last"] = snap["last"]
    out[f"{name}.min"] = snap["min"]
    out[f"{name}.max"] = snap["max"]
    out[f"{name}.mean"] = snap["mean"]
    out[f"{name}.count"] = snap["count"]


def drain_all_to_facets() -> dict[str, Any]:
    """Drain every reservoir into a flat dict of facet-name -> value.

    Called once at process exit from `_rank_root.close()` (triggered by
    destroy_process_group / unpatch / atexit). Empty reservoirs contribute
    no facets (a non-emitting metric simply doesn't appear in the dict).

    Also stamps model fingerprint facets (model.*) when _fingerprint_model
    has run. These are one-shot values set at framework init and are NOT
    reset by reset_all() — they persist across rotation drains.
    """
    out: dict[str, Any] = {}
    # Drain distributions
    with _lock:
        dist_names = list(_dist.keys())
    for name in dist_names:
        snap = drain_distribution(name)
        _emit_distribution_facets(name, snap, out)
    # Drain gauges
    with _lock:
        gauge_names = list(_gauge.keys())
    for name in gauge_names:
        snap = drain_gauge(name)
        _emit_gauge_facets(name, snap, out)
    # Drain counters (emit non-zero only)
    with _lock:
        counter_names = list(_counter.keys())
    for name in counter_names:
        n = drain_counter(name)
        if n:
            out[f"{name}_count"] = n
    # Stamp model fingerprint facets (one-shot, set at framework init).
    # Defensive: each extraction wrapped in try/except so a bad value
    # never crashes the rank close.
    try:
        if _model_fingerprinted:
            try:
                if _model_param_count > 0:
                    out["model.param_count"] = int(_model_param_count)
            except Exception:
                pass
            try:
                if _model_trainable_param_count > 0:
                    out["model.trainable_param_count"] = int(_model_trainable_param_count)
            except Exception:
                pass
            try:
                out["model.is_transformer"] = "true" if _model_is_transformer else "false"
            except Exception:
                pass
            try:
                if _model_dtype:
                    out["model.dtype"] = str(_model_dtype)
            except Exception:
                pass
            try:
                if _model_layers > 0:
                    out["model.layers"] = int(_model_layers)
            except Exception:
                pass
            try:
                if _model_hidden_dim > 0:
                    out["model.hidden_dim"] = int(_model_hidden_dim)
            except Exception:
                pass
            try:
                if _model_active_param_count > 0 and _model_active_param_count != _model_param_count:
                    out["model.active_param_count"] = int(_model_active_param_count)
            except Exception:
                pass
    except Exception:
        pass
    return out


def reset_all() -> None:
    """Test helper. Production code uses the per-name drain functions."""
    global _dist, _gauge, _counter
    with _lock:
        _dist = {}
        _gauge = {}
        _counter = {}


def _reset_child_state() -> None:
    """Fork handler. Mirrors the resets in `_metrics.py` / `_rank_root.py`."""
    global _dist, _gauge, _counter, _lock, _step_tls
    global _model_fingerprinted, _model_param_count, _model_trainable_param_count
    global _model_active_param_count
    global _model_layers, _model_hidden_dim, _model_is_transformer, _model_dtype
    global _embedding_hook_attached, _last_seq_len, _mfu_oversized_warned
    global _moe_modules_cached
    _dist = {}
    _gauge = {}
    _counter = {}
    _lock = threading.Lock()
    _step_tls = threading.local()
    _model_fingerprinted = False
    _model_param_count = 0
    _model_trainable_param_count = 0
    _model_active_param_count = 0
    _model_layers = 0
    _model_hidden_dim = 0
    _model_is_transformer = False
    _model_dtype = "bfloat16"
    _embedding_hook_attached = False
    _last_seq_len = 0
    _mfu_oversized_warned = False
    _moe_modules_cached = None


if hasattr(os, "register_at_fork"):
    os.register_at_fork(after_in_child=_reset_child_state)


# ---------------------------------------------------------------------------
# Per-step accumulator (thread-local)
# ---------------------------------------------------------------------------


class StepAccumulator:
    """Per-step accumulator: tracks the durations of children that close
    before the step boundary. Reset on step close.
    """

    __slots__ = (
        "step_start_ns",
        "forward_total_ms",
        "backward_total_ms",
        "optim_step_ms",
        "grad_clip_ms",
        "data_fetch_ms",
        "tokens_this_step",
    )

    def __init__(self) -> None:
        self.step_start_ns: int = 0
        self.forward_total_ms: float = 0.0
        self.backward_total_ms: float = 0.0
        self.optim_step_ms: float = 0.0
        self.grad_clip_ms: float = 0.0
        self.data_fetch_ms: float = 0.0
        self.tokens_this_step: int = 0


_step_tls = threading.local()


def get_step_accumulator() -> StepAccumulator:
    """Return the per-thread StepAccumulator, creating one if absent."""
    acc = getattr(_step_tls, "acc", None)
    if acc is None:
        acc = StepAccumulator()
        _step_tls.acc = acc
    return acc


def reset_step_accumulator() -> None:
    """Replace the per-thread accumulator with a fresh one."""
    global _last_seq_len
    _step_tls.acc = StepAccumulator()
    _last_seq_len = 0


def _fingerprint_model(model) -> None:
    """One-shot walk to capture invariants used by MFU computation.

    Idempotent: subsequent calls are no-ops. Best-effort: any exception
    leaves _model_fingerprinted=False so a later call can retry.

    Architecture detection: counts modules with transformer-like class
    names. Hidden dim is the modal in_features across all nn.Linear
    layers (good proxy for the model's primary embedding width).

    AIDEV-NOTE: _model_dtype is set from params[0].dtype as a fallback;
    _detect_compute_dtype() is called at step-close time to override with
    the AMP autocast dtype when mixed-precision is active.
    """
    global _model_fingerprinted, _model_param_count, _model_trainable_param_count
    global _model_active_param_count
    global _model_layers, _model_hidden_dim, _model_is_transformer, _model_dtype

    if _model_fingerprinted:
        return
    try:
        params = list(model.parameters())
        _model_param_count = sum(p.numel() for p in params)
        _model_trainable_param_count = sum(p.numel() for p in params if p.requires_grad)
        if params:
            try:
                _model_dtype = str(params[0].dtype).replace("torch.", "")
            except Exception:
                pass
    except Exception:
        return

    layers = 0
    hidden_dims = []
    try:
        import torch  # noqa: PLC0415

        for m in model.modules():
            tname = type(m).__name__
            if tname in (
                "TransformerEncoderLayer",
                "TransformerDecoderLayer",
                "LlamaDecoderLayer",
                "GPT2Block",
                "BertLayer",
                "T5Block",
            ):
                layers += 1
            elif hasattr(m, "self_attn") and hasattr(m, "mlp"):
                # Generic transformer block heuristic.
                layers += 1
            if isinstance(m, torch.nn.Linear):
                hidden_dims.append(m.in_features)
        if hidden_dims:
            from collections import Counter  # noqa: PLC0415

            _model_hidden_dim = Counter(hidden_dims).most_common(1)[0][0]
    except Exception:
        pass

    _model_layers = layers
    _model_is_transformer = layers > 0
    # _model_active_param_count defaults to total; updated after MoE detection
    # in _attach_layer2_to_inner_module via _estimate_moe_active_param_count.
    _model_active_param_count = _model_param_count
    _model_fingerprinted = True


def record_embedding_input(input_shape) -> None:
    """Called by an nn.Embedding forward hook (first Embedding only).
    Adds tokens (product of input shape dims) to the per-step accumulator.
    Also caches the sequence length (last dim of input_shape) for the
    attention quadratic term in MFU computation.
    """
    global _last_seq_len
    try:
        if len(input_shape) >= 2:
            _last_seq_len = int(input_shape[-1])
        n = 1
        for d in input_shape:
            n *= int(d)
        get_step_accumulator().tokens_this_step += n
    except Exception:
        pass


def attach_embedding_token_hook(model) -> None:
    """Find the FIRST nn.Embedding in the model and register a forward
    pre-hook that captures token count (B × T). Subsequent Embeddings
    skipped to avoid double-counting (transformer architectures often
    have token + position embeddings).
    """
    global _embedding_hook_attached
    if _embedding_hook_attached:
        return
    try:
        import torch  # noqa: PLC0415

        for m in model.modules():
            if isinstance(m, torch.nn.Embedding):

                def _hook(module, inputs):
                    if inputs and hasattr(inputs[0], "shape"):
                        record_embedding_input(inputs[0].shape)

                m.register_forward_pre_hook(_hook)
                _embedding_hook_attached = True
                return
    except Exception:
        pass


def _detect_moe_modules(model) -> list:
    """One-shot walk of model.modules() to collect MoE layers.

    Idempotent. Class-name match across the three main MoE libraries
    (DeepSpeed-MoE, Megatron-MoE, fairscale). Returns the list of module
    references found (possibly empty).
    """
    global _moe_modules_cached
    if _moe_modules_cached is not None:
        return _moe_modules_cached
    found = []
    try:
        for m in model.modules():
            if type(m).__name__ in _MOE_CLASS_NAMES:
                found.append(m)
    except Exception:
        return []
    _moe_modules_cached = found
    return found


def _detect_compute_dtype() -> str:
    """Best-effort detection of the actual compute dtype.

    For mixed-precision training the model's ``params[0].dtype`` is fp32
    (master copy), but the actual compute happens in bf16/fp16 under
    ``torch.autocast``. Read the autocast state if active; fall back to
    the param dtype cached at fingerprint time.

    AIDEV-NOTE: Called at close_step_to_summary time (per step) because
    autocast can be enabled/disabled per-step in advanced patterns.
    """
    try:
        import torch  # noqa: PLC0415

        if torch.is_autocast_enabled():
            ac_dtype = str(torch.get_autocast_gpu_dtype()).replace("torch.", "")
            return ac_dtype
    except Exception:
        pass
    return _model_dtype  # whatever was cached at fingerprint time


def _estimate_moe_active_param_count(model=None) -> int:
    """Estimate the number of params active per token through MoE layers.

    For top-K routing across N experts, only K/N of expert params are
    active per token. Heuristic: detect num_experts via ``num_experts``
    attribute or expert-counts length; assume top-2 by default
    (industry common); compute active fraction; subtract inactive
    expert params from total.

    Returns ``_model_param_count`` if no MoE modules or can't estimate.

    AIDEV-NOTE: ``model`` is accepted but not used — the function reads
    from ``_moe_modules_cached`` which was populated by ``_detect_moe_modules``.
    The parameter exists to keep the call signature consistent with the
    design description.
    """
    if not _moe_modules_cached:
        return _model_param_count
    try:
        # Assume same routing across all MoE layers in the model.
        first_moe = _moe_modules_cached[0]
        num_experts = (
            getattr(first_moe, "num_experts", None)
            or getattr(first_moe, "n_experts", None)
            or len(getattr(first_moe, "experts", []) or [])
            or None
        )
        if not num_experts or num_experts <= 1:
            return _model_param_count
        top_k = (
            getattr(first_moe, "top_k", None) or getattr(first_moe, "k", None) or 2  # industry default for top-K MoE
        )
        active_fraction = min(1.0, top_k / num_experts)
        # Estimate expert param count by walking experts attribute.
        expert_params = 0
        for moe in _moe_modules_cached:
            experts = getattr(moe, "experts", None)
            if experts is not None:
                for ex in experts:
                    try:
                        expert_params += sum(p.numel() for p in ex.parameters())
                    except Exception:
                        pass
        if expert_params == 0:
            return _model_param_count
        # Non-expert params (attention, embedding, layernorm, etc.) participate fully.
        non_expert = _model_param_count - expert_params
        return int(non_expert + active_fraction * expert_params)
    except Exception:
        return _model_param_count


def _coerce_int(value) -> Optional[int]:
    """Defensive scalar coercion: handles plain int, torch tensor (sum
    of expert counts), and iterables.
    """
    if value is None:
        return None
    try:
        if hasattr(value, "sum") and callable(value.sum):
            try:
                return int(value.sum().item())
            except Exception:
                pass
        if hasattr(value, "__iter__"):
            try:
                return int(sum(value))
            except Exception:
                pass
        return int(value)
    except Exception:
        return None


def read_moe_drop_ratio() -> Optional[float]:
    """Walk cached MoE modules, read per-step token counters, return the
    average drop ratio (dropped / total). Returns None when:

    - No MoE modules cached (no MoE in model)
    - Counters not available on any module
    - Total tokens routed = 0 (no forward yet this step)
    """
    if not _moe_modules_cached:
        return None
    total = 0
    routed = 0
    for module in _moe_modules_cached:
        # Read routed-token count (sum of per-expert counts).
        for attr in _MOE_ROUTED_ATTRS:
            counts = getattr(module, attr, None)
            n = _coerce_int(counts)
            if n is not None:
                routed += n
                break
        # Read total-input-token count.
        for attr in _MOE_TOTAL_ATTRS:
            tn = getattr(module, attr, None)
            n = _coerce_int(tn)
            if n is not None:
                total += n
                break
    if total <= 0:
        return None
    return max(0.0, (total - routed) / total)


def sample_memory_at_step_close() -> None:
    """Sample current + peak GPU memory at a step boundary. Feeds two
    gauge reservoirs:

    - `memory.gpu_used_memory_bytes`
    - `memory.peak_gpu_used_memory_bytes`

    Cheap: both calls read PyTorch's internal allocator state, no driver
    RPC. ~100 ns each.

    No-op when CUDA is unavailable or torch.cuda raises.
    """
    try:
        import torch  # noqa: PLC0415

        if not torch.cuda.is_available():
            return
        allocated = torch.cuda.memory_allocated()
        push_gauge("memory.gpu_used_memory_bytes", float(allocated))
        peak = torch.cuda.max_memory_allocated()
        push_gauge("memory.peak_gpu_used_memory_bytes", float(peak))
    except Exception:
        pass


def close_step_to_summary(prev_step_end_ns: int, now_ns_val: int) -> None:
    """Compute step duration + per-component durations from the
    accumulator and feed reservoirs. Called from the designated
    ``Optimizer.step`` close path. On the first step (no prior
    optimizer-end timestamp), step_duration is not recorded.

    AIDEV-NOTE: When prev_step_end_ns=0 (first step or skipped AMP step),
    step.duration_ms is intentionally NOT emitted. However, per-component
    metrics (forward_ms, backward_ms, data_fetch_ms, grad_clip_ms, optim_step_ms)
    ARE still drained from the accumulator and pushed to their reservoirs when
    non-zero. This is the correct behaviour for skipped AMP steps: the forward
    and backward activity genuinely happened (just no successful optimizer step
    boundary), so those durations should appear in the per-component distributions.
    """
    import os as _os  # noqa: PLC0415

    from ddtrace.internal.utils.formats import asbool  # noqa: PLC0415

    acc = get_step_accumulator()
    # step.duration_ms only makes sense when there is a prior step boundary.
    step_ms = 0.0
    if prev_step_end_ns > 0:
        step_ms = (now_ns_val - prev_step_end_ns) / 1e6
        push_distribution("step.duration_ms", step_ms)

    # Per-component metrics: always drain from accumulator (they represent real
    # work that happened, regardless of whether the step boundary is valid).
    # close_step_to_summary is the SOLE emitter for these metrics; the hook
    # sites only write to the accumulator (N1 dedupe contract).
    if acc.forward_total_ms > 0:
        push_distribution("step.forward_ms", acc.forward_total_ms)
    if acc.backward_total_ms > 0:
        push_distribution("step.backward_ms", acc.backward_total_ms)
    if acc.optim_step_ms > 0:
        push_distribution("step.optim_step_ms", acc.optim_step_ms)
    if acc.data_fetch_ms > 0:
        push_distribution("step.data_fetch_ms", acc.data_fetch_ms)
    if acc.grad_clip_ms > 0:
        push_distribution("step.grad_clip_ms", acc.grad_clip_ms)

    if prev_step_end_ns > 0:
        # MFU / Tflops — transformer-shaped models only, gated on
        # DD_PYTORCH_MFU_ENABLED (default true).
        # AIDEV-NOTE: Skip when prerequisites missing (non-transformer, no
        # tokens captured, no step duration). Tflops can emit without a peak
        # divisor; MFU requires a matched GPU+dtype entry in the lookup table.
        mfu_enabled = asbool(_os.environ.get("DD_PYTORCH_MFU_ENABLED", "true"))
        if mfu_enabled and _model_is_transformer and acc.tokens_this_step > 0 and step_ms > 0:
            try:
                import logging as _logging  # noqa: PLC0415

                from ddtrace.contrib.internal.pytorch._device import get as _device_get  # noqa: PLC0415
                from ddtrace.contrib.internal.pytorch._device import lookup_peak_flops  # noqa: PLC0415

                # Use AMP autocast dtype when active; fall back to param dtype.
                # AIDEV-NOTE: Params[0].dtype is fp32 for AMP master copies
                # even when compute runs in bf16/fp16 — using the autocast dtype
                # corrects the peak-FLOPS lookup from fp32 to bf16/fp16.
                dtype = _detect_compute_dtype()

                # Use active param count (adjusted for MoE top-K routing).
                effective_param_count = _model_active_param_count or _model_param_count

                # Chinchilla approximation (dominant term):
                # FLOPs per token ≈ 6 × param_count
                flops_per_token = 6.0 * effective_param_count
                # Attention quadratic term: 12 × num_layers × seq_len × hidden_dim.
                # Material for long sequences (seq_len ≳ 2K with hidden_dim ≲ 4K+).
                if _model_layers > 0 and _model_hidden_dim > 0 and _last_seq_len > 0:
                    flops_per_token += 12.0 * _model_layers * _last_seq_len * _model_hidden_dim
                step_s = step_ms / 1000.0
                achieved = flops_per_token * acc.tokens_this_step / step_s
                tflops = achieved / 1e12
                push_distribution("train.tflops", tflops)
                info = _device_get()
                gpu_name = getattr(info, "gpu_name", None) if info else None
                peak = lookup_peak_flops(gpu_name, dtype)
                if peak:
                    mfu = achieved / peak
                    # Emit raw (uncapped) value for diagnosis.
                    push_distribution("train.mfu_raw", mfu)
                    # Cap displayed MFU at 1.0 to avoid misleading UI values.
                    push_distribution("train.mfu", min(mfu, 1.0))
                    global _mfu_oversized_warned
                    if mfu > 1.5 and not _mfu_oversized_warned:
                        _logging.getLogger(__name__).warning(
                            "pytorch: computed MFU=%.2f exceeds 1.0 (capping displayed value). "
                            "Check model dtype (%s) and param count (%d) — common causes: "
                            "mixed precision (params reported as fp32 master copy but compute "
                            "is bf16/fp16) or MoE total-vs-active params. Set "
                            "DD_PYTORCH_MFU_ENABLED=false to disable.",
                            mfu,
                            dtype,
                            effective_param_count,
                        )
                        _mfu_oversized_warned = True
                    if 0 < acc.data_fetch_ms < step_ms:
                        compute_s = (step_ms - acc.data_fetch_ms) / 1000.0
                        achieved_compute = flops_per_token * acc.tokens_this_step / compute_s
                        mfu_excl = achieved_compute / peak
                        push_distribution("train.mfu_exclude_dataload_raw", mfu_excl)
                        push_distribution("train.mfu_exclude_dataload", min(mfu_excl, 1.0))
            except Exception:
                pass

        # MoE dropped-token ratio (when MoE modules present).
        ratio = read_moe_drop_ratio()
        if ratio is not None:
            push_distribution("train.avg_dropped_tokens", ratio)

    sample_memory_at_step_close()
    reset_step_accumulator()
