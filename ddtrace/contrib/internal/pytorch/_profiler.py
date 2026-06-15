"""Layer 3 CUDA kernel profiling bridge for the PyTorch contrib integration.

Gated by ``DD_PYTORCH_KERNEL_PROFILING=true``. Bridges ``torch.profiler``
events into Datadog traces by emitting ``pytorch.kernel`` spans as children
of the ``pytorch.step`` (Layer 2) span that issued them. Runs on a windowed
capture schedule (wait/warmup/active) so overhead is bounded.
"""

from collections import namedtuple
import os
import threading
import time
from typing import Any
from typing import Optional

from ddtrace import config
from ddtrace import tracer
from ddtrace.contrib.internal.pytorch._utils import compute_clock_offset_ns
from ddtrace.contrib.internal.pytorch._utils import set_training_job_id_tag
from ddtrace.contrib.internal.trace_utils import int_service
from ddtrace.internal.logger import get_logger
from ddtrace.internal.settings import env
from ddtrace.internal.utils.formats import asbool


log = get_logger(__name__)

# AIDEV-NOTE: Layer 3 is fully gated. When the env var is false (default),
# no torch.profiler symbol is referenced and the profiler state stays at its
# zero-value defaults.
KERNEL_PROFILING_ENABLED = asbool(env.get("DD_PYTORCH_KERNEL_PROFILING", "false"))


_SCHEDULE_DEFAULTS = {
    "DD_PYTORCH_PROFILE_WAIT_STEPS": 99,
    "DD_PYTORCH_PROFILE_WARMUP_STEPS": 1,
    "DD_PYTORCH_PROFILE_ACTIVE_STEPS": 5,
}


def _read_int_env(name: str, default: int) -> int:
    raw = env.get(name)
    if raw is None:
        return default
    try:
        value = int(raw)
    except (TypeError, ValueError):
        log.warning("Invalid integer for %s=%r; falling back to default %d", name, raw, default)
        return default
    # `wait` is allowed to be 0; warmup/active must be >= 1.
    if name == "DD_PYTORCH_PROFILE_WAIT_STEPS":
        if value < 0:
            log.warning("%s must be >= 0 (got %d); falling back to default %d", name, value, default)
            return default
        return value
    if value < 1:
        log.warning("%s must be >= 1 (got %d); falling back to default %d", name, value, default)
        return default
    return value


def _read_schedule_config():
    return (
        _read_int_env("DD_PYTORCH_PROFILE_WAIT_STEPS", _SCHEDULE_DEFAULTS["DD_PYTORCH_PROFILE_WAIT_STEPS"]),
        _read_int_env("DD_PYTORCH_PROFILE_WARMUP_STEPS", _SCHEDULE_DEFAULTS["DD_PYTORCH_PROFILE_WARMUP_STEPS"]),
        _read_int_env("DD_PYTORCH_PROFILE_ACTIVE_STEPS", _SCHEDULE_DEFAULTS["DD_PYTORCH_PROFILE_ACTIVE_STEPS"]),
    )


RingBufferEntry = namedtuple(
    "RingBufferEntry",
    ["step", "rank", "trace_id", "span_id", "start_ns", "end_ns"],
)


class StepRingBuffer:
    """Bounded thread-safe buffer of completed pytorch.step span windows.

    AIDEV-NOTE: Capacity is bounded at startup to keep the buffer from
    growing unboundedly while preserving entries needed by the active
    window. ``find_for_timestamp`` is a linear scan; N is small.
    """

    def __init__(self, capacity: int):
        self._capacity = capacity
        self._entries: list = []
        self._lock = threading.Lock()

    def __len__(self) -> int:
        with self._lock:
            return len(self._entries)

    def append(self, entry: RingBufferEntry) -> None:
        with self._lock:
            self._entries.append(entry)
            while len(self._entries) > self._capacity:
                self._entries.pop(0)

    def find_for_timestamp(self, ts_ns: int) -> Optional[RingBufferEntry]:
        with self._lock:
            for e in self._entries:
                if e.start_ns <= ts_ns <= e.end_ns:
                    return e
            return None

    def snapshot(self) -> list:
        with self._lock:
            return list(self._entries)


class _ProfilerState:
    def __init__(self):
        self.profiler: Any = None
        self.ring_buffer: Optional[StepRingBuffer] = None
        self.miss_counter: int = 0
        self.miss_log_every: int = 50
        self.clock_offset_ns: int = 0
        self.last_offset_refresh_ns: int = 0
        # Re-compute the offset every M minutes (configurable via env).
        self.offset_refresh_interval_ns: int = (
            _read_int_env("DD_PYTORCH_PROFILE_OFFSET_REFRESH_SEC", 600) * 1_000_000_000
        )
        self.lock = threading.Lock()


_STATE = _ProfilerState()

# Safety margin multiplier for the ring buffer capacity.
_SAFETY_MARGIN_MULTIPLIER = 2


def _reset_child_state() -> None:
    # AIDEV-NOTE: Drop the inherited profiler / lock; the parent's CUPTI
    # threads are gone in the child and stopping them would race the parent.
    global _STATE
    _STATE = _ProfilerState()


if hasattr(os, "register_at_fork"):
    os.register_at_fork(after_in_child=_reset_child_state)


def _resolve_activities():
    """Pick `ProfilerActivity` values available on this torch build."""
    import torch.profiler as tp

    activities = [tp.ProfilerActivity.CPU]
    try:
        import torch

        if torch.cuda.is_available():
            activities.append(tp.ProfilerActivity.CUDA)
    except Exception:
        pass
    return activities


def _build_torch_profiler(activities, schedule, on_trace_ready):
    """Construct the underlying ``torch.profiler.profile`` instance.

    Indirected for testability — unit tests monkeypatch this to a fake
    profiler so they don't pay the cost (or platform requirements) of the
    real one.
    """
    # AIDEV-NOTE: Imported lazily so the contrib package doesn't pull in
    # torch.profiler at module load when Layer 3 is gated off.
    import torch.profiler as tp

    return tp.profile(activities=activities, schedule=schedule, on_trace_ready=on_trace_ready)


def ensure_profiler_started(rank: int) -> None:
    if not KERNEL_PROFILING_ENABLED:
        return
    with _STATE.lock:
        if _STATE.profiler is not None:
            return
        wait, warmup, active = _read_schedule_config()
        capacity = wait + warmup + active + _SAFETY_MARGIN_MULTIPLIER * active
        _STATE.ring_buffer = StepRingBuffer(capacity=capacity)
        prof = None
        try:
            import torch.profiler as tp

            schedule = tp.schedule(wait=wait, warmup=warmup, active=active)
            activities = _resolve_activities()
            prof = _build_torch_profiler(
                activities=activities,
                schedule=schedule,
                on_trace_ready=_make_on_trace_ready(rank=rank),
            )
            prof.start()
            # Publish the profiler only after start succeeds so the
            # except clause below can unambiguously detect partial state
            # by checking ``_STATE.profiler is None``.
            _STATE.profiler = prof
        except Exception:
            # AIDEV-NOTE: Unsupported-platform fallback (CPU-only torch,
            # missing CUPTI) must never crash training. If start() partially
            # initialized CUPTI subscribers, attempt a best-effort stop so
            # we don't leak event sinks.
            log.debug("pytorch: torch.profiler unavailable; skipping kernel profiling", exc_info=True)
            if prof is not None and _STATE.profiler is None:
                try:
                    prof.stop()
                except Exception:
                    log.debug("pytorch: rollback prof.stop raised", exc_info=True)
            _STATE.profiler = None
            _STATE.ring_buffer = None


def shutdown_profiler() -> None:
    with _STATE.lock:
        prof = _STATE.profiler
        _STATE.profiler = None
        if prof is None:
            return
        try:
            prof.stop()
        except Exception:
            log.debug("torch.profiler stop() raised; suppressing", exc_info=True)


def _maybe_refresh_clock_offset() -> None:
    """Re-compute the wall-clock offset periodically.

    Retained for backward compatibility with tests and any external
    callers. ``_on_trace_ready`` now derives the per-batch offset
    directly from the ring buffer + Kineto event timestamps and ignores
    ``_STATE.clock_offset_ns``, so this routine is a no-op in the
    production code path; production correctness no longer depends on
    it being called.
    """
    now_ns = time.time_ns()
    with _STATE.lock:
        if now_ns - _STATE.last_offset_refresh_ns < _STATE.offset_refresh_interval_ns:
            return
        _STATE.last_offset_refresh_ns = now_ns
    try:
        new_offset = compute_clock_offset_ns().offset_ns
    except Exception:
        log.debug("compute_clock_offset_ns raised; keeping prior offset", exc_info=True)
        return
    with _STATE.lock:
        _STATE.clock_offset_ns = new_offset


def on_designated_step_finished(span, step: int, rank: int) -> None:
    """Called from `_hooks._maybe_close_step` after the designated optimizer's
    ``pytorch.step`` closes.

    Drives the torch.profiler schedule (one logical step = one ``prof.step()``
    call) and records the completed step's window in the ring buffer for
    `on_trace_ready` to correlate kernels against.
    """
    if not KERNEL_PROFILING_ENABLED:
        return
    ensure_profiler_started(rank=rank)
    prof = _STATE.profiler
    if prof is not None:
        try:
            prof.step()
        except Exception:
            log.debug("torch.profiler step() raised; suppressing", exc_info=True)
    buf = _STATE.ring_buffer
    if buf is None:
        return
    try:
        start_ns = int(span.start_ns)
        duration_ns = int(getattr(span, "duration_ns", 0) or 0)
        end_ns = start_ns + duration_ns
        buf.append(
            RingBufferEntry(
                step=step,
                rank=rank,
                trace_id=span.trace_id,
                span_id=span.span_id,
                start_ns=start_ns,
                end_ns=end_ns,
            )
        )
    except Exception:
        log.debug("pytorch: ring buffer append failed", exc_info=True)


def _make_on_trace_ready(rank: int):
    def _cb(prof):
        try:
            _on_trace_ready(prof, rank=rank)
        except Exception:
            log.debug("pytorch: on_trace_ready raised; suppressing", exc_info=True)

    return _cb


def _is_kernel_event(ev) -> bool:
    # AIDEV-NOTE: Only attribute CUDA-side kernel events. CPU op events are
    # ignored — Layer 2 spans already cover CPU compute.
    device = getattr(ev, "device_type", None)
    if device is None:
        return True
    return str(device).lower().endswith("cuda")


def _on_trace_ready(prof, rank: int) -> None:
    buf = _STATE.ring_buffer
    if buf is None:
        return
    # `ev.time_range` values are microseconds RELATIVE TO THE ACTIVE-PHASE
    # START (not profile start and not perf_counter). We can't anchor to
    # `time.time_ns()` at trace-ready entry — CUPTI/Kineto buffers flush
    # asynchronously, so on_trace_ready fires ~1-2s AFTER the kernels
    # actually executed. Instead, use the latest ring-buffer step's
    # ``end_ns`` as the active-phase end (the last ``prof.step()`` of
    # the active window closed that step), then back-compute the
    # phase-start wall clock from the maximum event-end raw_us.
    try:
        events = list(prof.events())
    except Exception:
        log.debug("prof.events() raised; suppressing", exc_info=True)
        return
    max_end_us = 0
    for ev in events:
        if not _is_kernel_event(ev):
            continue
        try:
            e = int(ev.time_range.end)
        except Exception:
            continue
        if e > max_end_us:
            max_end_us = e
    try:
        rb_snap_anchor = buf.snapshot()
    except Exception:
        rb_snap_anchor = []
    latest_step_end_ns = max((e.end_ns for e in rb_snap_anchor), default=time.time_ns())
    offset_ns = latest_step_end_ns - max_end_us * 1000
    kept = 0
    for ev in events:
        try:
            if not _is_kernel_event(ev):
                continue
            raw_us = int(ev.time_range.start)
            start_ns = raw_us * 1000 + offset_ns
            end_ns = int(ev.time_range.end) * 1000 + offset_ns
            entry = buf.find_for_timestamp(start_ns)
            if entry is None:
                with _STATE.lock:
                    _STATE.miss_counter += 1
                    miss_now = _STATE.miss_counter
                if miss_now % _STATE.miss_log_every == 0:
                    log.warning(
                        "pytorch: Layer 3 dropped %d kernel events (no matching pytorch.step window)",
                        miss_now,
                    )
                continue
            _emit_kernel_span(event=ev, entry=entry, start_ns=start_ns, end_ns=end_ns, rank=rank)
            kept += 1
        except Exception:
            log.debug("pytorch: kernel event handling raised; suppressing", exc_info=True)
    # Kernel spans are created from the profiler's `on_trace_ready`
    # callback, which runs on a CUPTI/Kineto worker thread — the tracer's
    # writer-loop won't necessarily ship them before the worker process
    # exits. Force a flush after each batch that produced spans.
    if kept:
        try:
            _get_tracer().flush()
        except Exception:
            log.debug("pytorch: tracer.flush after kernel batch failed", exc_info=True)


def _get_tracer():
    return tracer


def _emit_kernel_span(event, entry: RingBufferEntry, start_ns: int, end_ns: int, rank: int) -> None:
    """Emit a `pytorch.kernel` span as a child of the originating `pytorch.step`.

    AIDEV-NOTE: We use `child_of=Context(...)` (not a live Span) because the
    parent `pytorch.step` has already finished by the time `on_trace_ready`
    fires. The Context carries trace_id and span_id; ``training_job.id`` and
    ``job_id`` are set explicitly via ``set_training_job_id_tag`` so the span
    carries both canonical and legacy tags consistent with all other PyTorch
    span emitters.
    """
    from ddtrace._trace.context import Context
    from ddtrace.constants import USER_KEEP

    tr = _get_tracer()
    # AIDEV-NOTE: Force-keep kernel spans; the parent `pytorch.step` has
    # already been flushed by the time on_trace_ready fires, so without an
    # explicit sampling decision the kernel children can be dropped while
    # the parent persists (orphan spans).
    ctx = Context(trace_id=entry.trace_id, span_id=entry.span_id, sampling_priority=USER_KEEP)
    span = tr.start_span(
        "pytorch.kernel",
        service=int_service(None, config.pytorch),
        child_of=ctx,
        activate=False,
    )
    try:
        span.set_tag("kernel.name", str(getattr(event, "name", "unknown")))
        span.set_tag("debug.level", "3")
        stream_id = getattr(event, "stream", None)
        if stream_id is not None:
            span.set_tag("stream_id", str(stream_id))
        duration_ms = max(0.0, (end_ns - start_ns) / 1_000_000.0)
        span._set_attribute("duration_ms", duration_ms)
        flops = getattr(event, "flops", None)
        if flops:
            span._set_attribute("flops", float(flops))
        span._set_attribute("step", entry.step)
        span._set_attribute("rank", rank)
        set_training_job_id_tag(span)
        # Align span timestamps to the actual kernel interval.
        span.start_ns = start_ns
    finally:
        span.finish(finish_time=end_ns / 1_000_000_000.0)
