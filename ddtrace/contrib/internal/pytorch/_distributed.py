import collections
import os
import sys
import threading
import time
from typing import Any
from typing import Optional
import weakref

import torch
import wrapt

from ddtrace import config
from ddtrace import tracer
from ddtrace.contrib.internal.pytorch._utils import _amp_skip_state
from ddtrace.contrib.internal.pytorch._utils import _enter_framework
from ddtrace.contrib.internal.pytorch._utils import _get_active_framework
from ddtrace.contrib.internal.pytorch._utils import (
    _instrumentation_bypass,  # noqa: F401  (re-exported for grad_comm hook)
)
from ddtrace.contrib.internal.pytorch._utils import _should_record_cuda_event
from ddtrace.contrib.internal.pytorch._utils import get_cached_job_id
from ddtrace.contrib.internal.pytorch._utils import is_instrumentation_bypassed
from ddtrace.contrib.internal.pytorch._utils import job_id_env_set
from ddtrace.contrib.internal.pytorch._utils import register_framework
from ddtrace.contrib.internal.pytorch._utils import resolve_job_id_from_env
from ddtrace.contrib.internal.pytorch._utils import set_cached_job_id
from ddtrace.contrib.internal.pytorch._utils import set_training_job_id_tag
from ddtrace.contrib.internal.trace_utils import unwrap as _unwrap
from ddtrace.contrib.internal.trace_utils import wrap as _wrap
from ddtrace.internal.logger import get_logger
from ddtrace.internal.settings import env


log = get_logger(__name__)

_DEFAULT_CAPACITY = 1024
_OVERFLOW_WARN_EVERY = 64

# Default 1-in-N collective GPU sampling rate for summary reservoirs.
_DEFAULT_GPU_SAMPLE_RATE = 100


# Forward-declared here so the fork handler can reset them before
# Tasks 6 and 7 introduce their real assignments.
_no_env_job_id_warned: bool = False
_layer2_installed: bool = False


class _SummaryEventMarker:
    """Sentinel tuple substitute for resolver entries that should feed
    a summary reservoir instead of finishing a span.

    AIDEV-NOTE: Held in the same queue as span-keyed entries so a single
    resolver background thread services both paths. The isinstance check in
    _drain_ready / _finish_unresolved distinguishes the two.
    """

    __slots__ = ("op_name",)

    def __init__(self, op_name: str) -> None:
        self.op_name = op_name


def _detect_launcher() -> Optional[str]:
    """Best-effort identification of the distributed launcher.

    All probes are env reads; called once at `pytorch.rank` span open and
    never re-read. Returns None when no launcher signature is detected.
    """
    if env.get("TORCHELASTIC_RUN_ID"):
        return "torchrun"
    if env.get("_RAY_SUBMISSION_ID") or env.get("RAY_JOB_ID"):
        return "ray"
    if env.get("SLURM_JOB_ID"):
        return "slurm"
    if env.get("KUBEFLOW_TRAINING_JOB_ID"):
        return "kubeflow"
    return None


_cached_distributed_backend: Optional[str] = None


def _get_cached_backend() -> Optional[str]:
    """One-shot lookup of `torch.distributed.get_backend()`. Caches the
    result on first successful call. The backend (nccl/gloo/mpi) does not
    change during the lifetime of a process group.
    """
    global _cached_distributed_backend
    if _cached_distributed_backend is not None:
        return _cached_distributed_backend
    try:
        if torch.distributed.is_available() and torch.distributed.is_initialized():
            _cached_distributed_backend = str(torch.distributed.get_backend())
    except Exception:
        return None
    return _cached_distributed_backend


_state: dict[str, Any] = {
    "bootstrapped": False,
    "job_id": None,
    "rank": 0,
    "world_size": 1,
    "resolver": None,
    "rate_ticker": None,
    "gpu_sample_rate": _DEFAULT_GPU_SAMPLE_RATE,
    "gpu_sample_count": 0,
}
# Serializes the "bootstrapped" check so concurrent `init_process_group`
# calls don't double-run the bootstrap.
_bootstrap_lock = threading.Lock()


def _reset_child_state() -> None:
    # AIDEV-NOTE: Mutate `_state` in place so by-reference imports see the
    # reset. Locks are rebound — an inherited held lock would deadlock.
    global _bootstrap_lock
    _state.clear()
    _state.update(
        {
            "bootstrapped": False,
            "job_id": None,
            "rank": 0,
            "world_size": 1,
            "resolver": None,
            "rate_ticker": None,
            # AIDEV-NOTE: gpu_sample_rate is a config constant and stays; only
            # the counter is reset so the child doesn't inherit the parent's
            # partially-advanced sample position.
            "gpu_sample_rate": _DEFAULT_GPU_SAMPLE_RATE,
            "gpu_sample_count": 0,
        }
    )
    _bootstrap_lock = threading.Lock()
    # AIDEV-NOTE: Keep _installed after fork; inherited wrappers are still
    # active in the child.
    # AIDEV-NOTE: Inherited WeakSet entries reference parent-process DDP
    # instances that don't survive fork.
    _lazy_comm_hook_installed.clear()
    # Reset one-time-warning + Layer-2 install flag so the child can
    # re-emit / re-install as appropriate for its own bootstrap.
    global _no_env_job_id_warned, _layer2_installed, _cached_distributed_backend
    _no_env_job_id_warned = False
    _layer2_installed = False
    _cached_distributed_backend = None


if hasattr(os, "register_at_fork"):
    os.register_at_fork(after_in_child=_reset_child_state)


class CudaEventResolver:
    """Background polling of ``torch.cuda.Event`` pairs.

    Spans are held open until ``end_event.query()`` returns True; we then
    compute ``start_event.elapsed_time(end_event)`` (ms) and finish the span.
    Overflow drops the oldest entry and finishes that span with
    ``_dd.error_reason="cuda_event_overflow"``. Shutdown joins the thread with
    a bounded timeout and finishes any remaining spans with
    ``_dd.error_reason="cuda_event_unresolved"``.
    """

    def __init__(self, poll_interval: float = 0.005, capacity: int = _DEFAULT_CAPACITY):
        self._poll_interval = poll_interval
        self._capacity = capacity
        self._queue: collections.deque = collections.deque()
        self._lock = threading.Lock()
        self._stop_event = threading.Event()
        self._thread = None
        self._overflow_count = 0

    def start(self) -> None:
        if self._thread is not None:
            return
        self._stop_event.clear()
        t = threading.Thread(target=self._run, name="dd-pytorch-cuda-resolver", daemon=True)
        self._thread = t
        t.start()

    def submit(self, span, start_event, end_event) -> None:
        # Hold the lock only to mutate the queue / counter. Span finalization
        # (set_tag + finish) runs outside the lock because it may invoke user
        # span processors, exporters, or even recursively re-enter the resolver.
        evicted = None
        overflow_total = 0
        with self._lock:
            if len(self._queue) >= self._capacity:
                evicted, _, _ = self._queue.popleft()
                self._overflow_count += 1
                overflow_total = self._overflow_count
            self._queue.append((span, start_event, end_event))
        if evicted is not None:
            if overflow_total % _OVERFLOW_WARN_EVERY == 1:
                log.warning(
                    "pytorch: cuda event queue overflow (count=%d); dropping oldest span",
                    overflow_total,
                )
            self._finish_unresolved(evicted, reason="cuda_event_overflow")

    def submit_for_summary(self, op_name: str, start_event, end_event) -> None:
        """Like ``submit()``, but on resolution the elapsed GPU time is
        pushed to the ``collective.<op>.gpu_duration_ms`` summary
        reservoir. No span is finished.

        AIDEV-NOTE: Shares the same bounded queue as span entries so the
        resolver thread's single poll loop services both paths. Overflow
        drops the oldest entry silently (no span to finish, no user-visible
        error tag to set).
        """
        evicted = None
        with self._lock:
            if len(self._queue) >= self._capacity:
                evicted, _, _ = self._queue.popleft()
                self._overflow_count += 1
            self._queue.append((_SummaryEventMarker(op_name), start_event, end_event))
        if evicted is not None:
            self._finish_unresolved(evicted, reason="cuda_event_overflow")

    def _run(self) -> None:
        # Use stop_event.wait so stop() interrupts the sleep promptly instead
        # of waiting up to poll_interval before exiting.
        while not self._stop_event.wait(self._poll_interval):
            self._drain_ready()
        self._drain_ready()
        self._flush_remaining()

    def _drain_ready(self) -> None:
        with self._lock:
            pending = list(self._queue)
            self._queue.clear()
        keep: collections.deque = collections.deque()
        for entry, start, end in pending:
            try:
                ready = end.query()
            except Exception:
                self._finish_unresolved(entry, reason="cuda_event_query_error")
                continue
            if ready:
                if isinstance(entry, _SummaryEventMarker):
                    # Summary path: push GPU duration to the per-op reservoir.
                    try:
                        duration_ms = float(start.elapsed_time(end))
                        from ddtrace.contrib.internal.pytorch import _summary  # noqa: PLC0415

                        _summary.push_distribution(
                            f"collective.{entry.op_name}.gpu_duration_ms",
                            duration_ms,
                        )
                    except Exception:
                        pass
                else:
                    # Existing span path: set gpu.duration_ms tag and finish.
                    span = entry
                    try:
                        duration_ms = float(start.elapsed_time(end))
                        span._set_attribute("gpu.duration_ms", duration_ms)
                    except Exception:
                        span.set_tag("_dd.error_reason", "cuda_event_elapsed_error")
                    span.finish()
            else:
                keep.append((entry, start, end))
        with self._lock:
            keep.extend(self._queue)
            self._queue = keep

    def _flush_remaining(self) -> None:
        with self._lock:
            remaining = list(self._queue)
            self._queue.clear()
        for entry, _, _ in remaining:
            self._finish_unresolved(entry, reason="cuda_event_unresolved")

    @staticmethod
    def _finish_unresolved(entry, reason: str) -> None:
        if isinstance(entry, _SummaryEventMarker):
            # Drop silently; summary markers have no span to finish and no
            # error tag to surface. Overflow is logged at the submit site.
            return
        try:
            entry.set_tag("_dd.error_reason", reason)
        finally:
            entry.finish()

    def stop(self, timeout: float = 2.0) -> None:
        self._stop_event.set()
        t = self._thread
        if t is not None:
            t.join(timeout=timeout)
        self._thread = None
        self._flush_remaining()


def _bootstrap_distributed() -> None:
    """Capture rank/world_size and resolve the shared job id from env.

    AIDEV-NOTE: We do NOT perform any cross-rank broadcast. The previous
    rank-0 -> all-ranks `broadcast_object_list` introduced an unsafe
    collective on the default group that could pair with subsequent
    user collectives if it timed out and completed later. Removing it
    eliminates the race entirely. Cross-rank correlation now requires
    an env-supplied id (DD_PYTORCH_JOB_ID, RAY_JOB_ID,
    TORCHELASTIC_RUN_ID, KUBEFLOW_TRAINING_JOB_ID, SLURM_JOB_ID) —
    already the case for every supported launcher. When no env id is
    resolved we emit a one-time WARNING and intentionally leave
    `training_job.id` unset on spans so the missing correlation is
    visible in the UI rather than masked by per-rank UUIDs.
    """
    global _no_env_job_id_warned

    cached = get_cached_job_id()
    env_id_present = job_id_env_set()
    if cached:
        _state["job_id"] = cached
    else:
        _state["job_id"] = resolve_job_id_from_env()

    try:
        if torch.distributed.is_available() and torch.distributed.is_initialized():
            _state["rank"] = torch.distributed.get_rank()
            _state["world_size"] = torch.distributed.get_world_size()
    except Exception:
        log.exception("pytorch: failed to capture rank/world_size; defaulting to single-rank")

    publishable_job_id: Optional[str] = _state["job_id"]
    if not cached and not env_id_present:
        publishable_job_id = None
        if not _no_env_job_id_warned:
            log.warning(
                "pytorch: no shared training job id resolved from env "
                "(DD_PYTORCH_JOB_ID, RAY_JOB_ID, TORCHELASTIC_RUN_ID, "
                "KUBEFLOW_TRAINING_JOB_ID, SLURM_JOB_ID). Cross-rank "
                "trace correlation will be DISABLED for this run — spans "
                "will not carry the training_job.id tag. Set "
                "DD_PYTORCH_JOB_ID to a stable per-job value to enable "
                "correlation."
            )
            _no_env_job_id_warned = True

    if publishable_job_id is not None:
        set_cached_job_id(publishable_job_id, is_default=True)

    from ddtrace.contrib.internal.pytorch import _device  # noqa: PLC0415
    from ddtrace.contrib.internal.pytorch import _metrics  # noqa: PLC0415
    from ddtrace.contrib.internal.pytorch import _rank_root  # noqa: PLC0415

    try:
        _device.discover(local_rank=int(_state["rank"] or 0))
    except Exception:
        log.exception("pytorch: device discovery failed")

    try:
        _rank_root.open(
            rank=int(_state["rank"] or 0),
            world_size=int(_state["world_size"] or 1),
            framework=_get_active_framework() or "none",
            training_job_id=publishable_job_id,
        )
    except Exception:
        log.exception("pytorch: rank-root span open failed")

    try:
        ticker = _metrics.RateTicker(interval_s=float(config.pytorch.rate_ticker_interval_s))
        ticker.start()
        _state["rate_ticker"] = ticker
    except Exception:
        log.exception("pytorch: rate ticker start failed")
        _state["rate_ticker"] = None

    # Read collective GPU sample rate. Defaults to 100 (1-in-100 collectives
    # sampled for GPU timing when Layer One is off). Setting to 0 disables
    # summary-mode CUDA event sampling entirely.
    sample_rate = _DEFAULT_GPU_SAMPLE_RATE
    try:
        raw = env.get("DD_PYTORCH_COLLECTIVE_GPU_SAMPLE_RATE", "")
        if raw:
            sample_rate = int(raw)
    except Exception:
        log.debug("pytorch: invalid DD_PYTORCH_COLLECTIVE_GPU_SAMPLE_RATE; using default %d", _DEFAULT_GPU_SAMPLE_RATE)
    _state["gpu_sample_rate"] = sample_rate
    _state["gpu_sample_count"] = 0

    layer_one_on = config.pytorch.collective_trace_enabled
    # Start the resolver when Layer One is on (span path) OR when GPU summary
    # sampling is enabled (sample_rate > 0). The same background thread
    # serves both entry types.
    needs_resolver = layer_one_on or (sample_rate > 0)
    if needs_resolver and torch.cuda.is_available():
        try:
            resolver = CudaEventResolver()
            resolver.start()
            _state["resolver"] = resolver
        except Exception:
            log.exception("pytorch: failed to start CUDA event resolver")
            _state["resolver"] = None
    else:
        _state["resolver"] = None


def _wrapped_init_process_group(wrapped, instance, args, kwargs):
    result = wrapped(*args, **kwargs)
    with _bootstrap_lock:
        already = _state["bootstrapped"]
        if not already:
            _state["bootstrapped"] = True
    if not already:
        try:
            _bootstrap_distributed()
        except Exception:
            log.exception("pytorch: distributed bootstrap failed")
    return result


def _wrapped_destroy_process_group(wrapped, instance, args, kwargs):
    """Close the rank-root span before delegating to the original.

    AIDEV-NOTE: This is the canonical "rank is done with distributed
    training" signal — well-behaved frameworks (Lightning, HF Trainer,
    manual scripts) call it before process exit. Closing here gives us a
    deterministic flush point that doesn't depend on atexit ordering, and
    bounds the `pytorch.rank` span to actual distributed-training time
    instead of "until process exit" (which may include unrelated
    post-training work like checkpointing or evaluation).

    AIDEV-NOTE: Close happens *before* the wrapped call so that a NCCL
    teardown error (re-init mid-run, already-destroyed group, etc.) still
    yields a finished rank span with its `collective.summary` tag —
    losing visibility on exactly the run that hit teardown trouble would
    be the worst-case outcome.
    """
    try:
        from ddtrace.contrib.internal.pytorch import _rank_root  # noqa: PLC0415

        _rank_root.close()
    except Exception:
        log.debug("pytorch: rank-root close raised in destroy_process_group wrapper", exc_info=True)
    return wrapped(*args, **kwargs)


# (span_name, tensor_arg_index, group_arg_index). For collectives whose first
# positional is a list/aggregated tensor, point at the input arg so `bytes`
# reflects the full communication volume (the list-aware `_tensor_bytes` sums
# entries):
#   all_reduce(tensor, op, group, async_op)             -> tensor at 0, group at 2
#   all_gather(tensor_list, tensor, group, async_op)    -> input at 1, group at 2
#   broadcast(tensor, src, group, async_op)             -> tensor at 0, group at 2
#   reduce_scatter(output, input_list, op, group, ...)  -> input at 1, group at 3
#   barrier(group, async_op, device_ids)                -> tensor n/a, group at 0
# `group_arg_index` lets the wrapper read a positionally-passed ProcessGroup
# without relying on kwargs (PyTorch's signatures allow either).
_COLLECTIVES: dict[str, tuple[str, int, int]] = {
    "all_reduce": ("pytorch.allreduce", 0, 2),
    "all_gather": ("pytorch.allgather", 1, 2),
    "broadcast": ("pytorch.broadcast", 0, 2),
    "reduce_scatter": ("pytorch.reducescatter", 1, 3),
    "barrier": ("pytorch.barrier", 0, 0),
}


def _tensor_bytes(tensor) -> int:
    """Best-effort byte count for a single tensor or arbitrarily-nested
    list/tuple of tensors.

    AIDEV-NOTE: Iterative, with a depth cap — collectives are on the hot
    path and FSDP/DeepSpeed sometimes pass nested lists.
    """
    if not isinstance(tensor, (list, tuple)):
        try:
            return int(tensor.numel()) * int(tensor.element_size())
        except Exception:
            return 0
    total = 0
    stack = list(tensor)
    depth_budget = 32
    while stack and depth_budget > 0:
        item = stack.pop()
        if isinstance(item, (list, tuple)):
            stack.extend(item)
            depth_budget -= 1
        else:
            try:
                total += int(item.numel()) * int(item.element_size())
            except Exception:
                pass
    return total


def _extract_group(args: tuple, kwargs: dict, group_arg_index: int):
    """Read the ProcessGroup from kwargs (preferred) or positional args.

    PyTorch's collective signatures allow `group` to be passed either way; the
    wrapper must check both so multi-process-group jobs that pass `group`
    positionally still get correct CUDA-event gating in Layer One.
    """
    if "group" in kwargs:
        return kwargs["group"]
    if 0 <= group_arg_index < len(args):
        return args[group_arg_index]
    return None


def _make_collective_wrapper(span_name: str, tensor_arg_index: int = 0, group_arg_index: int = -1):
    # AIDEV-NOTE: Layer Zero always emits per-call metrics; Layer One
    # additionally opens a span and (on CUDA) a deferred-resolved event
    # pair. Wall-clock duration approximates async collectives; precise
    # GPU timing requires Layer One. `torch.compile` may bypass this wrap.
    # AIDEV-NOTE: When Layer One is OFF and gpu_sample_rate > 0, we
    # independently sample 1-in-N collectives for GPU timing and push
    # elapsed_time into the summary reservoir — no span opened, no L1 cost.
    from ddtrace.contrib.internal.pytorch import _metrics  # closure capture

    def wrapper(wrapped, instance, args, kwargs):
        if is_instrumentation_bypassed():
            return wrapped(*args, **kwargs)
        tensor = args[tensor_arg_index] if len(args) > tensor_arg_index else None
        group = _extract_group(args, kwargs, group_arg_index)
        bytes_count = _tensor_bytes(tensor) if (tensor is not None and span_name != "pytorch.barrier") else 0
        op_name = span_name.split(".", 1)[1] if "." in span_name else span_name

        # Layer One gating: open the per-collective span and CUDA event pair
        # only when `DD_PYTORCH_COLLECTIVE_TRACE=true`.
        layer_one_on = bool(getattr(config.pytorch, "collective_trace_enabled", False))
        resolver = _state.get("resolver")
        span = None
        l1_resolver = resolver if layer_one_on else None
        start_event = end_event = None
        if layer_one_on:
            # `child_of=tracer.current_span()` ties the collective span to the
            # currently-active application span without making it the new active
            # span. We deliberately do NOT pass `activate=True` because the CUDA
            # path defers `span.finish()` to a background resolver thread; if the
            # span were active, it would remain so on the caller thread until the
            # async finish ran, mis-parenting any subsequent spans on this thread.
            span = tracer.start_span(
                span_name,
                service=config.pytorch.service,
                child_of=tracer.current_span(),
            )
            span.set_tag("framework", _get_active_framework() or "none")
            span.set_tag("debug.level", "1")
            span._set_attribute("rank", _state["rank"])
            span._set_attribute("world_size", _state["world_size"])
            set_training_job_id_tag(span)
            if tensor is not None and span_name != "pytorch.barrier":
                span._set_attribute("bytes", bytes_count)
            if l1_resolver is not None and _should_record_cuda_event(group, tensor):
                try:
                    start_event = torch.cuda.Event(enable_timing=True)
                    end_event = torch.cuda.Event(enable_timing=True)
                    start_event.record()
                except Exception:
                    start_event = end_event = None

        # Summary sampling path: only when Layer One is OFF. Record CUDA events
        # for 1-in-N collectives and submit to the resolver's summary track.
        # This gives precise GPU-side timing without requiring L1 spans.
        summary_start = summary_end = None
        cuda_eligible = (
            (not layer_one_on)
            and resolver is not None
            and tensor is not None
            and torch.cuda.is_available()
            and _should_record_cuda_event(group, tensor)
        )
        if cuda_eligible:
            sample_rate = _state.get("gpu_sample_rate", 0)
            if sample_rate > 0:
                cnt = _state.get("gpu_sample_count", 0) + 1
                _state["gpu_sample_count"] = cnt
                if cnt % sample_rate == 0:
                    try:
                        summary_start = torch.cuda.Event(enable_timing=True)
                        summary_end = torch.cuda.Event(enable_timing=True)
                        summary_start.record()
                    except Exception:
                        summary_start = summary_end = None

        wrapped_raised = False
        t0 = time.perf_counter_ns()
        try:
            return wrapped(*args, **kwargs)
        except BaseException:
            wrapped_raised = True
            if span is not None:
                span.set_exc_info(*sys.exc_info())
            raise
        finally:
            duration_ms = (time.perf_counter_ns() - t0) / 1e6
            try:
                _metrics.record_collective(op=op_name, duration_ms=duration_ms, bytes_count=bytes_count)
            except Exception:
                log.debug("pytorch: Layer Zero metric emit failed", exc_info=True)
            if span is not None:
                submitted = False
                if not wrapped_raised and start_event is not None and end_event is not None and l1_resolver is not None:
                    try:
                        end_event.record()
                        l1_resolver.submit(span, start_event, end_event)
                        submitted = True
                    except Exception:
                        span.set_tag("_dd.error_reason", "cuda_event_record_error")
                if not submitted:
                    # Release the recorded start_event when we won't submit it
                    # — otherwise the GPU event resource leaks until GC.
                    start_event = end_event = None
                    span.finish()
            # Summary sampling: record end event and submit to resolver for
            # the summary track. Only runs when L1 is off (cuda_eligible
            # already gates on `not layer_one_on`). Skip on exception to
            # avoid polluting the reservoir with incomplete timings.
            if not wrapped_raised and summary_start is not None and summary_end is not None:
                try:
                    summary_end.record()
                    resolver.submit_for_summary(op_name, summary_start, summary_end)
                except Exception:
                    pass

    return wrapper


def _distributed_available() -> bool:
    """True only when `torch.distributed` was compiled in (USE_DISTRIBUTED=1).

    On torch builds without distributed support, the collective APIs are
    absent and `_wrap` would raise — short-circuit the whole install path.
    """
    try:
        return bool(torch.distributed.is_available())
    except Exception:
        return False


def _install_collectives() -> None:
    if not _distributed_available():
        return
    for fn_name, (span_name, tensor_idx, group_idx) in _COLLECTIVES.items():
        if not hasattr(torch.distributed, fn_name):
            continue
        _wrap("torch.distributed", fn_name, _make_collective_wrapper(span_name, tensor_idx, group_idx))


def _uninstall_collectives() -> None:
    if not _distributed_available():
        return
    for fn_name in _COLLECTIVES:
        if not hasattr(torch.distributed, fn_name):
            continue
        try:
            _unwrap(torch.distributed, fn_name)
        except Exception:
            log.debug("pytorch: failed to unwrap torch.distributed.%s", fn_name, exc_info=True)


# FSDP-style functional collective variants. Available on torch >= 2.0 but
# wrapped behind `hasattr` because PyTorch sometimes ships these with internal
# underscored names or removes them in pre-release builds.
# Both take `(output_tensor, input_tensor, ...)`; we tag bytes from the input
# (per-rank contribution) at index 1.
#   all_gather_into_tensor(output_tensor, input_tensor, group, async_op)   -> group at 2
#   reduce_scatter_tensor(output, input, op, group, async_op)              -> group at 3
_FSDP_COLLECTIVES: dict[str, tuple[str, int, int]] = {
    "all_gather_into_tensor": ("pytorch.allgather_into_tensor", 1, 2),
    "reduce_scatter_tensor": ("pytorch.reducescatter_tensor", 1, 3),
}


def _install_fsdp_collectives() -> None:
    if not _distributed_available():
        return
    for fn_name, (span_name, tensor_idx, group_idx) in _FSDP_COLLECTIVES.items():
        if not hasattr(torch.distributed, fn_name):
            continue
        _wrap("torch.distributed", fn_name, _make_collective_wrapper(span_name, tensor_idx, group_idx))


def _uninstall_fsdp_collectives() -> None:
    if not _distributed_available():
        return
    for fn_name in _FSDP_COLLECTIVES:
        if not hasattr(torch.distributed, fn_name):
            continue
        try:
            _unwrap(torch.distributed, fn_name)
        except Exception:
            log.debug("pytorch: failed to unwrap torch.distributed.%s", fn_name, exc_info=True)


def _wrapped_ddp_init(wrapped, instance, args, kwargs):
    """Run the original DDP __init__ and register framework tags.

    AIDEV-NOTE: We deliberately do NOT call ``instance.register_comm_hook``
    here. PyTorch allows exactly one comm hook per DDP instance, and
    pre-registering ours would break user code that installs PowerSGD,
    compression, or custom hooks. When the user does register a hook,
    our `register_comm_hook` wrap (see `_wrapped_register_comm_hook`)
    chains their hook with the timing path. When the user does not
    register a hook, DDP uses its native fused C++ allreduce path with
    zero ddtrace overhead — but `pytorch.grad_comm` spans are not
    emitted for those users (deliberate tradeoff; the previous default
    was breaking valid user setups).
    """
    result = wrapped(*args, **kwargs)
    try:
        register_framework(instance, "ddp")
    except Exception:
        log.warning("pytorch: failed to register DDP framework", exc_info=True)
    try:
        from ddtrace.contrib.internal.pytorch import _rank_root  # noqa: PLC0415

        _rank_root.set_framework("ddp")
    except Exception:
        log.debug("pytorch: failed to update rank-root framework tag", exc_info=True)
    inner = getattr(instance, "module", instance)
    _attach_layer2_to_inner_module(inner)
    return result


def _install_ddp() -> None:
    try:
        import torch.nn.parallel.distributed  # noqa: F401
    except Exception:
        return
    if not hasattr(torch.nn.parallel.distributed, "DistributedDataParallel"):
        return
    _wrap(
        "torch.nn.parallel.distributed",
        "DistributedDataParallel.__init__",
        _wrapped_ddp_init,
    )


def _uninstall_ddp() -> None:
    try:
        import torch.nn.parallel.distributed  # noqa: F401
    except Exception:
        return
    if not hasattr(torch.nn.parallel.distributed, "DistributedDataParallel"):
        return
    try:
        _unwrap(torch.nn.parallel.distributed.DistributedDataParallel, "__init__")
    except Exception:
        log.debug("pytorch: failed to unwrap DDP.__init__", exc_info=True)


def _wrapped_fsdp_init(wrapped, instance, args, kwargs):
    result = wrapped(*args, **kwargs)
    try:
        register_framework(instance, "fsdp")
    except Exception:
        log.warning("pytorch: failed to register FSDP framework", exc_info=True)
    try:
        from ddtrace.contrib.internal.pytorch import _rank_root

        _rank_root.set_framework("fsdp")
    except Exception:
        log.debug("pytorch: failed to update rank-root framework tag", exc_info=True)
    # FSDP overrides `__getattr__` to delegate to `_fsdp_wrapped_module`; under
    # a mocked / partially-initialized instance that attribute may be missing
    # and the lookup itself raises, so wrap defensively.
    try:
        inner = getattr(instance, "module", instance)
    except Exception:
        inner = instance
    _attach_layer2_to_inner_module(inner)
    return result


def _wrapped_fsdp_forward(wrapped, instance, args, kwargs):
    """Open a framework context around FSDP forward so collectives emitted
    during the sharded forward pass are tagged `framework=fsdp`.
    """
    with _enter_framework(instance):
        return wrapped(*args, **kwargs)


def _install_fsdp() -> None:
    try:
        from torch.distributed.fsdp import FullyShardedDataParallel  # noqa: F401
    except Exception:
        return
    _wrap("torch.distributed.fsdp", "FullyShardedDataParallel.__init__", _wrapped_fsdp_init)
    _wrap("torch.distributed.fsdp", "FullyShardedDataParallel.forward", _wrapped_fsdp_forward)


def _uninstall_fsdp() -> None:
    try:
        from torch.distributed.fsdp import FullyShardedDataParallel
    except Exception:
        return
    for attr in ("__init__", "forward"):
        try:
            _unwrap(FullyShardedDataParallel, attr)
        except Exception:
            log.debug("pytorch: failed to unwrap FSDP.%s", attr, exc_info=True)


def _wrapped_deepspeed_init(wrapped, instance, args, kwargs):
    result = wrapped(*args, **kwargs)
    try:
        register_framework(instance, "deepspeed")
    except Exception:
        log.warning("pytorch: failed to register deepspeed framework", exc_info=True)
    try:
        from ddtrace.contrib.internal.pytorch import _rank_root

        _rank_root.set_framework("deepspeed")
    except Exception:
        log.debug("pytorch: failed to update rank-root framework tag", exc_info=True)
    # DeepSpeed exposes the user model on `.module`; fall back to the engine
    # itself if it isn't there.
    inner = getattr(instance, "module", instance)
    _attach_layer2_to_inner_module(inner)
    return result


def _attach_layer2_to_inner_module(model) -> None:
    """Lazy import + call into _hooks.attach_layer_two_hooks to avoid a
    circular import at module load. Also fingerprint the model and
    attach the embedding token hook for summary-mode MFU.

    AIDEV-NOTE: MoE active-param estimation runs after both fingerprinting
    and MoE detection so _model_param_count is already set when
    _estimate_moe_active_param_count reads it.
    """
    try:
        from ddtrace.contrib.internal.pytorch import _hooks

        _hooks.attach_layer_two_hooks(model)
    except Exception:
        log.debug("pytorch: layer2 attachment failed", exc_info=True)
    try:
        from ddtrace.contrib.internal.pytorch import _summary

        _summary._fingerprint_model(model)
        _summary.attach_embedding_token_hook(model)
        _summary._detect_moe_modules(model)
        # Update active param count after MoE detection.
        _summary._model_active_param_count = _summary._estimate_moe_active_param_count(model)
    except Exception:
        log.debug("pytorch: model fingerprinting failed", exc_info=True)


def _wrapped_deepspeed_method(name: str):
    def wrapper(wrapped, instance, args, kwargs):
        with _enter_framework(instance):
            return wrapped(*args, **kwargs)

    return wrapper


def _install_deepspeed() -> None:
    try:
        import deepspeed  # type: ignore[import-not-found]  # noqa: F401
    except Exception:
        return
    if not hasattr(deepspeed, "DeepSpeedEngine"):
        return
    _wrap("deepspeed", "DeepSpeedEngine.__init__", _wrapped_deepspeed_init)
    for m in ("forward", "backward", "step"):
        if hasattr(deepspeed.DeepSpeedEngine, m):
            _wrap("deepspeed", "DeepSpeedEngine.%s" % m, _wrapped_deepspeed_method(m))


def _uninstall_deepspeed() -> None:
    try:
        import deepspeed  # type: ignore[import-not-found]
    except Exception:
        return
    if not hasattr(deepspeed, "DeepSpeedEngine"):
        return
    for m in ("__init__", "forward", "backward", "step"):
        try:
            _unwrap(deepspeed.DeepSpeedEngine, m)
        except Exception:
            log.debug("pytorch: failed to unwrap DeepSpeedEngine.%s", m, exc_info=True)


# Stores the original `step` bound method for every wrapped optimizer. The
# WeakKeyDictionary auto-evicts entries for garbage-collected optimizers and
# also acts as our "is this instance wrapped" registry (presence ⇔ wrapped).
_step_originals: "weakref.WeakKeyDictionary[Any, Any]" = weakref.WeakKeyDictionary()


def _instance_step_wrapper(wrapped, instance, args, kwargs):
    # Lazy import to avoid circular import between _hooks and _distributed.
    from ddtrace.contrib.internal.pytorch import _hooks

    return _hooks.optimizer_step(wrapped, instance, args, kwargs)


def _wrapped_optimizer_init(wrapped, instance, args, kwargs):
    """Wrap `optimizer.step` per-instance after construction.

    Most PyTorch optimizers override `Optimizer.step`, so wrapping the base
    class method does NOT intercept subclass calls. Instance-level wrapping
    catches all optimizers without enumerating subclasses.

    AIDEV-NOTE: Gate on profiling_on OR summary_on (not just profiling_on).
    With the default DD_PYTORCH_SUMMARY_PROFILING=true / DD_PYTORCH_PROFILING=false
    configuration the instance step wrap must still attach so the summary path
    (LR gauge + optim_step_ms distribution) receives feeds per step.
    """
    result = wrapped(*args, **kwargs)
    from ddtrace.contrib.internal.pytorch import _hooks  # noqa: PLC0415

    profiling_on = _hooks.is_profiling_enabled()
    summary_on = bool(getattr(config.pytorch, "summary_profiling", False))
    if not (profiling_on or summary_on):
        return result
    try:
        if instance in _step_originals:
            # Already wrapped (e.g. base Optimizer.__init__ chained through a
            # subclass __init__ that also calls super().__init__()). Skip.
            return result
        original = instance.step
        _step_originals[instance] = original
        instance.step = wrapt.FunctionWrapper(original, _instance_step_wrapper)
    except Exception:
        log.warning("pytorch: failed to wrap optimizer.step", exc_info=True)
    return result


def _install_optimizer() -> None:
    # AIDEV-NOTE: Per-instance `step` wrap is gated on
    # `is_profiling_enabled()` at construction time so Layer-Two-off
    # workloads pay zero per-step overhead.
    _wrap("torch.optim.optimizer", "Optimizer.__init__", _wrapped_optimizer_init)


def _uninstall_optimizer() -> None:
    try:
        _unwrap(torch.optim.Optimizer, "__init__")
    except Exception:
        log.debug("pytorch: failed to unwrap Optimizer.__init__", exc_info=True)
    # Restore the original `step` on every already-constructed instance.
    for opt, original_step in list(_step_originals.items()):
        try:
            opt.step = original_step
        except Exception:
            log.debug("pytorch: failed to restore optimizer.step", exc_info=True)
    _step_originals.clear()


_wrapped_gradscaler_targets: list = []


def _wrapped_gradscaler_step(wrapped, instance, args, kwargs):
    """Toggle AMP state and notify Layer 2 of the step outcome.

    The inner optimizer.step (which we also wrap) sets
    ``_amp_skip_state.step_executed = True`` when it runs; if GradScaler
    detects an overflow and skips, the flag stays False.
    """
    prev_in_amp = getattr(_amp_skip_state, "in_amp", False)
    prev_step_executed = getattr(_amp_skip_state, "step_executed", False)
    _amp_skip_state.in_amp = True
    _amp_skip_state.step_executed = False
    optimizer = args[0] if args else kwargs.get("optimizer")
    raised = False
    try:
        return wrapped(*args, **kwargs)
    except BaseException:
        raised = True
        raise
    finally:
        skipped = not _amp_skip_state.step_executed
        _amp_skip_state.in_amp = prev_in_amp
        _amp_skip_state.step_executed = prev_step_executed
        # When the inner optimizer raised, leave the step open so the
        # next successful step closes it cleanly — emitting the outcome
        # here would mark a failed step as "skipped" and prematurely
        # close the parent `pytorch.step`.
        if not raised:
            try:
                from ddtrace.contrib.internal.pytorch import _hooks

                _hooks.gradscaler_emit_step_outcome(optimizer, skipped=skipped)
            except Exception:
                log.debug("pytorch: gradscaler outcome hook failed", exc_info=True)


def _install_gradscaler() -> None:
    # AIDEV-NOTE: id(cls) dedup — torch>=2.1 aliases torch.cuda.amp and
    # torch.amp GradScaler to the same class; double-wrapping nests
    # in_amp save/restore unnecessarily.
    seen_ids = set()
    candidates: list = []
    try:
        from torch.cuda.amp import GradScaler as CudaScaler  # noqa: F401

        candidates.append(("torch.cuda.amp", CudaScaler))
    except Exception:
        pass
    try:
        from torch.amp import GradScaler as AmpScaler  # noqa: F401

        candidates.append(("torch.amp", AmpScaler))
    except Exception:
        pass
    for module_path, cls in candidates:
        # Identity-based dedup: in torch >= 2.1, both names typically alias the
        # same class object, and double-wrapping `.step` corrupts the
        # in_amp flag on re-entry.
        if id(cls) in seen_ids:
            continue
        seen_ids.add(id(cls))
        try:
            _wrap(module_path, "GradScaler.step", _wrapped_gradscaler_step)
            _wrapped_gradscaler_targets.append(cls)
        except Exception:
            log.warning("pytorch: failed to wrap %s.GradScaler.step", module_path, exc_info=True)


def _uninstall_gradscaler() -> None:
    for cls in _wrapped_gradscaler_targets:
        try:
            _unwrap(cls, "step")
        except Exception:
            log.debug("pytorch: failed to unwrap GradScaler.step", exc_info=True)
    _wrapped_gradscaler_targets.clear()


# Tracks which DDP instances have already auto-registered a comm hook via the
# lazy `_pre_backward_hook` path. WeakSet so destroyed models are evicted
# automatically.
_lazy_comm_hook_installed: "weakref.WeakSet" = weakref.WeakSet()


# Active backward-span context, captured at the moment Tensor.backward opens
# its pytorch.backward span and cleared on backward exit. The DDP comm hook
# fires on a CUDA-callback / reducer-internal thread that doesn't share the
# autograd thread's active-span state, so we publish the context here so the
# hook can attach pytorch.grad_comm as a child of the live backward span.
#
# Under Ray Train there's exactly one rank per process and a single
# loss.backward() is in flight at any time (Python's autograd is serialized
# under the GIL), so a process-global slot suffices; we don't need a
# per-thread map. Reads from the hook thread are short and atomic enough on
# CPython that the lock is mostly there for documentation, but we keep it so
# the contract is obvious.
_backward_ctx_lock = threading.Lock()
_backward_ctx = None  # Optional[ddtrace._trace.context.Context]

# Set to True inside `_make_chained_comm_hook` when a grad_comm span
# finishes during a backward. `_wrapped_tensor_backward`'s finally
# reads and clears this; if True, it issues a single tracer.flush()
# so grad_comm spans (emitted on a CUDA-callback thread that does not
# share the backward thread's trace context) reach the writer before
# the backward returns. Process-global is acceptable for the same
# reason as `_backward_ctx`: single rank per process, one backward in
# flight at a time.
_grad_comm_emitted_in_backward: bool = False


def _set_active_backward_ctx(ctx) -> None:
    global _backward_ctx
    with _backward_ctx_lock:
        _backward_ctx = ctx


def _get_active_backward_ctx():
    with _backward_ctx_lock:
        return _backward_ctx


def _make_chained_comm_hook(user_hook):
    """Wrap a user-supplied DDP comm hook with our timing layer.

    Layer Zero metrics are always emitted (regardless of
    `collective_trace_enabled`). A per-bucket `pytorch.grad_comm` span is
    only opened when Layer One (`collective_trace_enabled`) is on.
    """
    from ddtrace.contrib.internal.pytorch import _metrics  # closure capture

    def chained(state, bucket):
        if not config.pytorch.grad_comm_enabled:
            return user_hook(state, bucket)

        # Layer Zero metrics: always emit (regardless of collective_trace_enabled).
        # AIDEV-NOTE: The inner `torch.distributed.all_reduce` is bypassed via
        # `_instrumentation_bypass` to avoid double-counting; we record the
        # grad-comm equivalent here so DDP gradient traffic still appears in
        # `pytorch.collective.*` metrics under op=grad_comm.
        bytes_count = 0
        try:
            if hasattr(bucket, "gradients"):
                grads = list(bucket.gradients())
                if grads:
                    bytes_count = sum(_tensor_bytes(t) for t in grads)
            elif hasattr(bucket, "buffer"):
                bytes_count = _tensor_bytes(bucket.buffer())
        except Exception:
            log.debug("pytorch: bucket size introspection failed", exc_info=True)

        # Layer One: open per-bucket span only if the user opted in.
        layer_one_on = bool(getattr(config.pytorch, "collective_trace_enabled", False))
        span = None
        if layer_one_on:
            # Attach grad_comm as a child of the currently-open
            # pytorch.backward span. The hook fires from a
            # CUDA-callback / reducer-internal thread that doesn't share
            # the autograd thread's active-span state, so reading
            # tracer.current_span() here would return None or the wrong
            # span. ``_wrapped_tensor_backward`` publishes the live
            # backward span's Context via ``_set_active_backward_ctx``
            # so we can pick it up here as the parent. If no backward
            # is active (DDP can fire comm hooks during warm-up before
            # the first user backward), fall back to a fresh trace root
            # — better than dropping the span.
            parent_ctx = _get_active_backward_ctx()
            span = tracer.start_span(
                "pytorch.grad_comm",
                service=config.pytorch.service,
                child_of=parent_ctx,
                activate=False,
            )
            # Force-keep: sampling priority is committed at first write,
            # so pinning USER_KEEP up front avoids the case where a
            # later ``manual.keep`` tag arrives too late if the writer
            # has already observed the context.
            try:
                from ddtrace.constants import USER_KEEP  # noqa: PLC0415

                span.context.sampling_priority = USER_KEEP
            except Exception:
                log.debug("pytorch: failed to pin grad_comm sampling_priority", exc_info=True)
            span.set_tag("framework", "ddp")
            span.set_tag("debug.level", "1")
            span._set_attribute("rank", _state["rank"])
            set_training_job_id_tag(span)
            if bytes_count:
                span._set_attribute("bytes", bytes_count)

        t0 = time.perf_counter_ns()
        try:
            with _instrumentation_bypass():
                return user_hook(state, bucket)
        finally:
            duration_ms = (time.perf_counter_ns() - t0) / 1e6
            try:
                _metrics.record_collective(op="grad_comm", duration_ms=duration_ms, bytes_count=bytes_count)
            except Exception:
                log.debug("pytorch: Layer Zero metric emit failed for grad_comm", exc_info=True)
            # Summary feeds — bucket-level distributions on pytorch.rank.
            try:
                from ddtrace.contrib.internal.pytorch import _summary  # noqa: PLC0415

                _summary.push_distribution("grad_comm.bucket_duration_ms", duration_ms)
                if bytes_count:
                    _summary.push_distribution("grad_comm.bytes_per_bucket", float(bytes_count))
            except Exception:
                pass
            if span is not None:
                try:
                    span.finish()
                except Exception:
                    log.debug("pytorch: grad_comm span finish failed", exc_info=True)
                # Mark that a grad_comm span fired so the enclosing
                # backward can flush once after returning. We do NOT
                # flush here — that would block the CUDA-callback
                # thread per bucket.
                global _grad_comm_emitted_in_backward
                _grad_comm_emitted_in_backward = True

    chained._dd_chained = True
    return chained


def _wrapped_register_comm_hook(wrapped, instance, args, kwargs):
    if not config.pytorch.grad_comm_enabled:
        return wrapped(*args, **kwargs)
    if len(args) >= 2:
        state, hook = args[0], args[1]
        rest = args[2:]
        return wrapped(state, _make_chained_comm_hook(hook), *rest, **kwargs)
    if "hook" in kwargs:
        kwargs = dict(kwargs)
        kwargs["hook"] = _make_chained_comm_hook(kwargs["hook"])
    return wrapped(*args, **kwargs)


def _default_allreduce_hook(state, bucket):
    """Fallback delegate preserving DDP's standard mean-reduction semantics."""
    try:
        from torch.distributed.algorithms.ddp_comm_hooks.default_hooks import allreduce_hook
    except Exception:
        return None
    return allreduce_hook(state, bucket)


def _wrapped_pre_backward_hook(wrapped, instance, args, kwargs):
    if instance not in _lazy_comm_hook_installed and config.pytorch.grad_comm_enabled:
        try:
            _lazy_comm_hook_installed.add(instance)
            instance.register_comm_hook(None, _default_allreduce_hook)
        except Exception:
            log.debug("pytorch: lazy comm hook registration failed", exc_info=True)
    return wrapped(*args, **kwargs)


def _install_ddp_comm_hook() -> None:
    try:
        import torch.nn.parallel.distributed  # noqa: F401
    except Exception:
        return
    cls = torch.nn.parallel.distributed.DistributedDataParallel
    if hasattr(cls, "register_comm_hook"):
        _wrap(
            "torch.nn.parallel.distributed",
            "DistributedDataParallel.register_comm_hook",
            _wrapped_register_comm_hook,
        )
    if hasattr(cls, "_pre_backward_hook"):
        _wrap(
            "torch.nn.parallel.distributed",
            "DistributedDataParallel._pre_backward_hook",
            _wrapped_pre_backward_hook,
        )
    else:
        log.debug(
            "pytorch: DistributedDataParallel._pre_backward_hook not found "
            "(PyTorch 2.0); lazy comm-hook registration disabled — "
            "DDP gradient communication will be traced via torch.distributed.all_reduce only"
        )


def _uninstall_ddp_comm_hook() -> None:
    try:
        import torch.nn.parallel.distributed  # noqa: F401
    except Exception:
        return
    cls = torch.nn.parallel.distributed.DistributedDataParallel
    for attr in ("register_comm_hook", "_pre_backward_hook"):
        if hasattr(cls, attr):
            try:
                _unwrap(cls, attr)
            except Exception:
                log.debug("pytorch: failed to unwrap DDP.%s", attr, exc_info=True)
    _lazy_comm_hook_installed.clear()


_installed: bool = False


def _should_install() -> bool:
    """Belt-and-suspenders gate: even when the operator opts in, skip
    installing per-call wrappers on processes that almost certainly
    will not run distributed training.

    Returns True when ANY of the following holds:
      - `DD_PYTORCH_FORCE_INSTALL=true` is set (escape hatch).
      - `RANK` or `WORLD_SIZE` env var is set (a launcher prepared the process).
      - `torch.distributed.is_available() and is_initialized()` is true
        (late-patch path, user is mid-run).
    """
    from ddtrace.internal.utils.formats import asbool  # noqa: PLC0415

    if asbool(env.get("DD_PYTORCH_FORCE_INSTALL", "false")):
        return True
    if env.get("RANK") or env.get("WORLD_SIZE"):
        return True
    try:
        if torch.distributed.is_available() and torch.distributed.is_initialized():
            return True
    except Exception:
        pass
    return False


# Reentrancy guard: PyTorch's `Tensor.backward` calls into
# `torch.autograd.backward` internally. Without this flag, a single
# `loss.backward()` would fire both wraps and double-count the duration
# (and emit two `pytorch.backward` spans in L2 mode). Thread-local so
# concurrent training threads stay independent.
_BACKWARD_REENTRANCY = threading.local()


def _capture_loss_if_scalar(tensor) -> None:
    """Push `train.loss` if `tensor` is a single-scalar tensor and capture is
    enabled. Caller is responsible for the `summary_feeds_on` gate.

    AIDEV-NOTE: GPU→host sync via `item()` is intentional and gated by
    `DD_PYTORCH_CAPTURE_LOSS` (default true). Callers can opt out.
    """
    from ddtrace.contrib.internal.pytorch import _summary  # noqa: PLC0415
    from ddtrace.internal.utils.formats import asbool  # noqa: PLC0415

    if not asbool(env.get("DD_PYTORCH_CAPTURE_LOSS", "true")):
        return
    try:
        if hasattr(tensor, "numel") and tensor.numel() == 1:
            _summary.push_distribution("train.loss", float(tensor.item()))
    except Exception:
        pass  # GPU sync failure / non-tensor / etc — skip silently


def _run_backward_with_instrumentation(wrapped, args, kwargs):
    """Shared instrumentation for both `Tensor.backward` and
    `torch.autograd.backward`:
    - opens a `pytorch.backward` span when Layer 2 profiling is enabled;
    - feeds the `backward_total_ms` summary accumulator;
    - publishes the active backward Context so the chained DDP comm hook
      can attach `pytorch.grad_comm` children;
    - issues a single `tracer.flush()` after the wrapped call if any
      `pytorch.grad_comm` span fired (DDP comm hooks run on a CUDA-callback
      thread whose trace context isn't shared with the autograd thread —
      without this flush, those spans can sit in the writer buffer).

    AIDEV-NOTE: `_grad_comm_emitted_in_backward` is a process-global flag.
    Overlapping backward calls (GAN-style training, reentrant backward) can
    race the flag — the worst outcome is that some grad_comm spans flush
    one backward later than ideal. We accept this trade-off.
    """
    from ddtrace.contrib.internal.pytorch import _summary  # noqa: PLC0415
    from ddtrace.contrib.internal.pytorch._hooks import _ensure_step_open  # noqa: PLC0415
    from ddtrace.contrib.internal.pytorch._hooks import _step_parent  # noqa: PLC0415
    from ddtrace.contrib.internal.pytorch._hooks import is_profiling_enabled  # noqa: PLC0415

    profiling_on = is_profiling_enabled()
    summary_on = bool(getattr(config.pytorch, "summary_profiling", False))
    # Gate: backward_total_ms accumulator is a summary-mode feature.
    # In pure Layer-1 mode (collective_trace=true, summary=false, profiling=false)
    # the wrap still runs for the grad_comm flush in the finally block, but
    # we must NOT fill reservoirs the user explicitly disabled.
    summary_feeds_on = summary_on or profiling_on

    t0 = time.perf_counter_ns()
    span = None
    if profiling_on:
        try:
            _ensure_step_open()
            span = tracer.start_span(
                "pytorch.backward",
                service=config.pytorch.service,
                child_of=_step_parent(),
            )
            span.set_tag("component", "pytorch")
            span.set_tag("debug.level", "2")
            span._set_attribute("rank", _state["rank"])
            set_training_job_id_tag(span)
        except Exception:
            log.debug("pytorch: failed to open pytorch.backward span", exc_info=True)
            span = None
        if span is not None:
            _set_active_backward_ctx(span.context)
    try:
        return wrapped(*args, **kwargs)
    finally:
        dt_ms = (time.perf_counter_ns() - t0) / 1e6
        if summary_feeds_on:
            try:
                _summary.get_step_accumulator().backward_total_ms += dt_ms
                # AIDEV-NOTE: Do NOT push_distribution("step.backward_ms") here.
                # close_step_to_summary is the sole emitter for step.backward_ms
                # (drains from acc.backward_total_ms). Pushing here AND there would
                # double the count facet — same dedupe contract as step.optim_step_ms.
            except Exception:
                pass
        if span is not None:
            _set_active_backward_ctx(None)
            try:
                span.finish()
            except Exception:
                log.debug("pytorch: pytorch.backward span finish failed", exc_info=True)
        global _grad_comm_emitted_in_backward
        if _grad_comm_emitted_in_backward:
            _grad_comm_emitted_in_backward = False
            try:
                tracer.flush()
            except Exception:
                log.debug("pytorch: late flush after backward failed", exc_info=True)


def _wrapped_tensor_backward(wrapped, instance, args, kwargs):
    """Wrap ``Tensor.backward`` — captures ``train.loss`` from the
    single-scalar loss tensor, then delegates to
    :func:`_run_backward_with_instrumentation`.
    """
    if getattr(_BACKWARD_REENTRANCY, "in_progress", False):
        # Already inside outer backward instrumentation (shouldn't normally
        # happen for Tensor.backward, but guard for paranoia / nested calls).
        return wrapped(*args, **kwargs)

    profiling_on = False
    try:
        from ddtrace.contrib.internal.pytorch._hooks import is_profiling_enabled  # noqa: PLC0415

        profiling_on = is_profiling_enabled()
    except Exception:
        pass
    summary_on = bool(getattr(config.pytorch, "summary_profiling", False))
    if summary_on or profiling_on:
        # Capture loss BEFORE backward runs so an exception during backward
        # doesn't make us miss the value.
        _capture_loss_if_scalar(instance)

    _BACKWARD_REENTRANCY.in_progress = True
    try:
        return _run_backward_with_instrumentation(wrapped, args, kwargs)
    finally:
        _BACKWARD_REENTRANCY.in_progress = False


def _wrapped_autograd_backward(wrapped, instance, args, kwargs):
    """Wrap ``torch.autograd.backward(tensors, ...)`` — covers the multi-tensor
    autograd entrypoint used by some custom training loops (GANs, multi-loss
    RL training) that don't go through ``Tensor.backward``.

    When called as the inner of ``Tensor.backward`` (PyTorch's standard
    implementation routes through this), the reentrancy guard delegates without
    re-instrumenting so we don't double-count duration or emit a second span.

    Loss capture: for the single-tensor list case (``backward([loss])``) the
    behaviour matches ``Tensor.backward``. For multi-tensor calls, no loss is
    captured (no single scalar — recording the sum would be misleading).
    """
    if getattr(_BACKWARD_REENTRANCY, "in_progress", False):
        return wrapped(*args, **kwargs)

    profiling_on = False
    try:
        from ddtrace.contrib.internal.pytorch._hooks import is_profiling_enabled  # noqa: PLC0415

        profiling_on = is_profiling_enabled()
    except Exception:
        pass
    summary_on = bool(getattr(config.pytorch, "summary_profiling", False))
    if summary_on or profiling_on:
        # Inspect args[0] for a single-scalar tensor. Accept both `backward(t)`
        # and `backward([t])` call shapes.
        try:
            first = args[0] if args else kwargs.get("tensors")
            if first is not None:
                if hasattr(first, "numel"):
                    _capture_loss_if_scalar(first)
                elif isinstance(first, (list, tuple)) and len(first) == 1:
                    _capture_loss_if_scalar(first[0])
        except Exception:
            pass

    _BACKWARD_REENTRANCY.in_progress = True
    try:
        return _run_backward_with_instrumentation(wrapped, args, kwargs)
    finally:
        _BACKWARD_REENTRANCY.in_progress = False


def _install_tensor_backward() -> None:
    if hasattr(torch, "Tensor") and hasattr(torch.Tensor, "backward"):
        try:
            _wrap("torch", "Tensor.backward", _wrapped_tensor_backward)
        except Exception:
            log.debug("pytorch: failed to wrap torch.Tensor.backward", exc_info=True)
    try:
        if hasattr(torch, "autograd") and hasattr(torch.autograd, "backward"):
            _wrap("torch.autograd", "backward", _wrapped_autograd_backward)
    except Exception:
        log.debug("pytorch: failed to wrap torch.autograd.backward", exc_info=True)


def _uninstall_tensor_backward() -> None:
    try:
        _unwrap(torch.Tensor, "backward")
    except Exception:
        log.debug("pytorch: failed to unwrap torch.Tensor.backward", exc_info=True)
    # torch.autograd is loaded once _install_tensor_backward wrapped it; no
    # explicit import here so we don't shadow the module-level `torch` name.
    try:
        _unwrap(torch.autograd, "backward")
    except Exception:
        log.debug("pytorch: failed to unwrap torch.autograd.backward", exc_info=True)


def _wrapped_clip_grad_norm(wrapped, instance, args, kwargs):
    """Capture `train.grad_norm` (return value) and `step.grad_clip_ms`
    (call duration). Summary-only feed — never opens a span.
    """
    from ddtrace.contrib.internal.pytorch import _summary  # noqa: PLC0415

    t0 = time.perf_counter_ns()
    raised = False
    result = None
    try:
        result = wrapped(*args, **kwargs)
    except BaseException:
        raised = True
        raise
    finally:
        dt_ms = (time.perf_counter_ns() - t0) / 1e6
        try:
            _summary.get_step_accumulator().grad_clip_ms = dt_ms
            # AIDEV-NOTE: Do NOT push_distribution("step.grad_clip_ms") here.
            # close_step_to_summary is the sole emitter for step.grad_clip_ms
            # (drains from acc.grad_clip_ms). Pushing here AND there would
            # double the count facet — same dedupe contract as step.optim_step_ms.
        except Exception:
            pass
    # Capture the returned norm (tensor or float). Only reached if no exception.
    if not raised:
        try:
            if hasattr(result, "item"):
                value = float(result.item())
            else:
                value = float(result)
            _summary.push_distribution("train.grad_norm", value)
        except Exception:
            pass
    return result


def _install_grad_clip() -> None:
    """Wrap `torch.nn.utils.clip_grad_norm_` for grad_norm + grad_clip_ms
    reservoirs. No-op if the function is missing (older torch).
    """
    try:
        import torch.nn.utils  # noqa: PLC0415, F401
    except Exception:
        return
    if not hasattr(torch.nn.utils, "clip_grad_norm_"):
        return
    try:
        _wrap("torch.nn.utils", "clip_grad_norm_", _wrapped_clip_grad_norm)
    except Exception:
        log.debug("pytorch: failed to wrap clip_grad_norm_", exc_info=True)


def _uninstall_grad_clip() -> None:
    try:
        import torch.nn.utils  # noqa: PLC0415

        _unwrap(torch.nn.utils, "clip_grad_norm_")
    except Exception:
        log.debug("pytorch: failed to unwrap clip_grad_norm_", exc_info=True)


def install() -> None:
    # AIDEV-NOTE: install() must be idempotent; wrapt stacks on repeated
    # wraps and _unwrap peels only the outermost layer. Flip _installed
    # only after the wrap sequence succeeds so a partial failure is retriable.
    global _installed
    if _installed:
        return
    if not _should_install():
        log.debug(
            "pytorch: install() skipped — process appears non-distributed "
            "(set DD_PYTORCH_FORCE_INSTALL=true to override)"
        )
        return
    try:
        if _distributed_available() and hasattr(torch.distributed, "init_process_group"):
            _wrap("torch.distributed", "init_process_group", _wrapped_init_process_group)
        if _distributed_available() and hasattr(torch.distributed, "destroy_process_group"):
            _wrap("torch.distributed", "destroy_process_group", _wrapped_destroy_process_group)
        _install_collectives()
        _install_fsdp_collectives()
        _install_ddp()
        _install_fsdp()
        _install_deepspeed()
        # Layer 2/3 wraps: install Optimizer / GradScaler only when
        # DD_PYTORCH_PROFILING=true at patch() time. Toggling the env
        # later requires unpatch() + patch() — covered by
        # `test_repatch_cycle_picks_up_profiling_toggle`.
        #
        # Tensor.backward is installed when EITHER profiling OR Layer One
        # is on. Layer One needs the wrapper's finally-block flush to
        # un-strand `pytorch.grad_comm` spans from the CUDA-callback
        # thread; without the install, those spans sit in the writer
        # buffer indefinitely.
        from ddtrace.contrib.internal.pytorch._hooks import is_profiling_enabled  # noqa: PLC0415

        global _layer2_installed
        profiling_on = is_profiling_enabled()
        layer_one_on = bool(getattr(config.pytorch, "collective_trace_enabled", False))
        summary_on = bool(getattr(config.pytorch, "summary_profiling", False))
        # Optimizer + GradScaler wraps: install when profiling OR summary on.
        # (Summary mode reads LR + optim_step_ms from these wraps.)
        if profiling_on or summary_on:
            _install_optimizer()
            _install_gradscaler()
            _layer2_installed = True
        # Tensor.backward + clip_grad_norm wraps: install when ANY mode is on.
        if profiling_on or layer_one_on or summary_on:
            _install_tensor_backward()
            # _install_grad_clip is summary-only — fires the grad_norm /
            # grad_clip_ms reservoir feeds. Install whenever Layer-2 wraps
            # install (or unconditionally; the wrap is cheap when no one
            # calls clip_grad_norm_).
            _install_grad_clip()
            _layer2_installed = True
        _install_ddp_comm_hook()
    except Exception:
        # Roll back any partial wraps so the next install() can try cleanly.
        try:
            if _layer2_installed:
                _uninstall_grad_clip()
                _uninstall_tensor_backward()
                _uninstall_gradscaler()
                _uninstall_optimizer()
                _layer2_installed = False
            _uninstall_ddp_comm_hook()
            _uninstall_deepspeed()
            _uninstall_fsdp()
            _uninstall_ddp()
            _uninstall_fsdp_collectives()
            _uninstall_collectives()
            if _distributed_available() and hasattr(torch.distributed, "destroy_process_group"):
                _unwrap(torch.distributed, "destroy_process_group")
            if _distributed_available() and hasattr(torch.distributed, "init_process_group"):
                _unwrap(torch.distributed, "init_process_group")
        except Exception:
            log.debug("pytorch: install rollback raised", exc_info=True)
        raise
    _installed = True
    # Late-patch bootstrap: if the user already called `init_process_group`
    # before `patch()`, our wrapper will never fire. Run the bootstrap now so
    # rank/world_size/job_id are populated and the CUDA event resolver is
    # started for already-initialized distributed jobs.
    if _distributed_available():
        try:
            if torch.distributed.is_initialized():
                with _bootstrap_lock:
                    already = _state["bootstrapped"]
                    if not already:
                        _state["bootstrapped"] = True
                if not already:
                    _bootstrap_distributed()
        except Exception:
            log.exception("pytorch: late-patch bootstrap failed")


def uninstall() -> None:
    # AIDEV-NOTE: Wrap teardown is gated on `_installed`; bootstrap-state
    # teardown runs unconditionally so direct `_bootstrap_distributed()`
    # callers can clean up.
    global _installed
    if _installed:
        _installed = False
        if _distributed_available() and hasattr(torch.distributed, "destroy_process_group"):
            try:
                _unwrap(torch.distributed, "destroy_process_group")
            except Exception:
                log.debug("pytorch: failed to unwrap destroy_process_group", exc_info=True)
        if _distributed_available() and hasattr(torch.distributed, "init_process_group"):
            try:
                _unwrap(torch.distributed, "init_process_group")
            except Exception:
                log.debug("pytorch: failed to unwrap init_process_group", exc_info=True)
        _uninstall_collectives()
        _uninstall_fsdp_collectives()
        _uninstall_ddp()
        _uninstall_fsdp()
        _uninstall_deepspeed()
        global _layer2_installed
        if _layer2_installed:
            _uninstall_optimizer()
            _uninstall_gradscaler()
            _uninstall_tensor_backward()
            _uninstall_grad_clip()
            _layer2_installed = False
        _uninstall_ddp_comm_hook()
    # Remove Layer 2 model hooks (no-op if Layer 2 was never enabled).
    try:
        from ddtrace.contrib.internal.pytorch import _hooks

        _hooks.detach_layer_two_hooks()
    except Exception:
        log.debug("pytorch: layer2 hook detachment failed", exc_info=True)
    resolver: Optional[CudaEventResolver] = _state.get("resolver")
    if resolver is not None:
        resolver.stop(timeout=2.0)
    ticker = _state.get("rate_ticker")
    if ticker is not None:
        try:
            ticker.stop(timeout=1.0)
        except Exception:
            log.debug("pytorch: rate ticker stop raised", exc_info=True)
    try:
        from ddtrace.contrib.internal.pytorch import _rank_root

        _rank_root.close()
    except Exception:
        log.debug("pytorch: rank-root close raised", exc_info=True)
    # AIDEV-NOTE: We intentionally do NOT reset `_device._cache` here. The
    # device id is a property of the physical GPU bound to this process and
    # does not change across patch/unpatch cycles. Re-running discovery on
    # the next bootstrap would produce the same value.
    _state.update(
        {
            "bootstrapped": False,
            "job_id": None,
            "rank": 0,
            "world_size": 1,
            "resolver": None,
            "rate_ticker": None,
            "gpu_sample_rate": _DEFAULT_GPU_SAMPLE_RATE,
            "gpu_sample_count": 0,
        }
    )
    # AIDEV-NOTE: Also clear the published job-id cache in _utils. Without
    # this, a subsequent patch() cycle reads the prior job's id from
    # `_default_job_id` / the TLS slot and silently emits new spans
    # under the old id — a re-patch cross-pollution bug.
    try:
        from ddtrace.contrib.internal.pytorch import _utils as _utils_mod  # noqa: PLC0415

        _utils_mod._default_job_id = None
        _utils_mod._tls_job_id = threading.local()
    except Exception:
        log.debug("pytorch: failed to reset cached job id on uninstall", exc_info=True)
    # Reset the one-time no-env-id warning so a subsequent patch() can
    # re-emit it if the operator continues to run without an env id.
    global _no_env_job_id_warned
    _no_env_job_id_warned = False
