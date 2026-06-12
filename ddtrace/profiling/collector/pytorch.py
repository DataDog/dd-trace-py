from __future__ import annotations

import abc
import logging
import random
from typing import Any

import wrapt

from ddtrace.internal.datadog.profiling import ddup
from ddtrace.internal.settings.profiling import config
from ddtrace.profiling import _threading
from ddtrace.profiling import collector
from ddtrace.trace import Tracer


LOG = logging.getLogger(__name__)

_NANOS_PER_MICROSECOND = 1e3


# Frames require a file name, but GPU frames are not from a Python file.
# We use the following as a placeholder.
_FILE_PLACEHOLDER = "<native>"

# We use a frame to group GPU frames under a device.
# This frame also needs a file name, none can really make sense.
_DEVICE_FRAME_FILE_NAME = "<torch>"


class _WrappedTorchProfiler(wrapt.ObjectProxy):
    def __init__(
        self,
        wrapped: Any,
        tracer: Tracer | None,
    ) -> None:
        wrapt.ObjectProxy.__init__(self, wrapped)
        self.on_trace_ready = _handle_torch_trace
        self._self_tracer = tracer


class MLProfilerCollector(collector.CaptureSamplerCollector):
    """Record ML framework (i.e. pytorch) profiler usage."""

    PROFILED_TORCH_CLASS: type

    def __init__(self) -> None:
        super().__init__()
        self.tracer: Tracer | None = None
        # Holds the pytorch profiler object which is wrapped by this class
        self._original: Any = None

    @abc.abstractmethod
    def _get_patch_target(self) -> Any:
        pass

    @abc.abstractmethod
    def _set_patch_target(self, value: Any) -> None:
        pass

    def _start_service(self) -> None:
        """Start collecting framework profiler usage."""
        try:
            import torch
        except ImportError as e:
            raise collector.CollectorUnavailable(e)
        self._torch_module = torch
        self.patch()
        super()._start_service()  # type: ignore[safe-super]

    def _stop_service(self) -> None:
        """Stop collecting framework profiler usage."""
        super()._stop_service()  # type: ignore[safe-super]
        self.unpatch()

    def patch(self) -> None:
        """Patch the module for tracking profiling data."""
        # We only patch the profile call from the `torch.profiler` module.
        self._original = self._get_patch_target()

        def profiler_init(wrapped: Any, instance: Any, args: Any, kwargs: Any) -> Any:
            profiler = wrapped(*args, **kwargs)
            return self.PROFILED_TORCH_CLASS(
                profiler,
                self.tracer,
            )

        self._set_patch_target(wrapt.FunctionWrapper(self._original, profiler_init))

    def unpatch(self) -> None:
        """Unpatch the torch.profiler module for tracking profiling data."""
        self._set_patch_target(self._original)


class TorchProfilerCollector(MLProfilerCollector):
    """Monkey patch torch.profiler.profile usage."""

    PROFILED_TORCH_CLASS = _WrappedTorchProfiler

    def __init__(self) -> None:
        super().__init__()

    def _get_patch_target(self) -> Any:
        return self._torch_module.profiler.profile

    def _set_patch_target(self, value: Any) -> None:
        self._torch_module.profiler.profile = value


def _handle_torch_trace(prof: Any) -> None:
    LOG.debug("_handle_torch_trace called")
    events = prof.events()
    if not events:
        return

    num_events = len(events)
    events_limit = config.pytorch.events_limit or 1_000_000

    # Subsample events if we exceed the limit
    if num_events > events_limit:
        LOG.debug("Dropped events. events_limit %d, len(events): %d", events_limit, num_events)
        events = random.sample(events, events_limit)  # nosec: used for sampling, not security

    # Determine which attributes to use once (avoid per-event getattr checks).
    # For CPU operators we use the "self" (exclusive of children) variants so that,
    # once we reconstruct the operator call tree below, parent frames aggregate to
    # their inclusive totals without double counting their children. CUDA device
    # events are leaves and report 0 for the "self" device metrics (they are async),
    # so for those we use the event's own totals instead.
    sample_event = events[0]
    self_gpu_time_attr = (
        "self_device_time_total" if hasattr(sample_event, "self_device_time_total") else "self_cuda_time_total"
    )
    total_gpu_time_attr = "device_time_total" if hasattr(sample_event, "device_time_total") else "cuda_time_total"
    self_gpu_memory_attr = (
        "self_device_memory_usage" if hasattr(sample_event, "self_device_memory_usage") else "self_cuda_memory_usage"
    )
    total_gpu_memory_attr = (
        "device_memory_usage" if hasattr(sample_event, "device_memory_usage") else "cuda_memory_usage"
    )

    # Earlier PyTorch versions use microseconds, later versions use nanoseconds
    kineto_results = prof.profiler.kineto_results
    if hasattr(kineto_results, "trace_start_ns"):
        trace_start_ns = kineto_results.trace_start_ns()
    elif hasattr(kineto_results, "trace_start_us"):
        trace_start_ns = kineto_results.trace_start_us() * _NANOS_PER_MICROSECOND
    else:
        raise AttributeError("Neither trace_start_ns nor trace_start_us exists")

    # Cache thread info lookups (many events share the same thread).
    # Key includes device_type and device_index because a single thread_id can appear
    # in both CPU and CUDA events, or across multiple CUDA devices with different names.
    thread_info_cache: dict[tuple[int, str, int], tuple[int, str]] = {}
    empty_events_count = 0

    for e in events:
        handle = ddup.SampleHandle()
        data_added = False

        # Number of times this event aggregates. The time metrics below are
        # totals across those occurrences, so we divide by count to recover the
        # per-occurrence value and let ddup re-multiply by count.
        count: int = e.count or 1

        # Cache str(device_type) since we use it multiple times. CPU operators get
        # the call tree reconstructed and use exclusive ("self") metrics; CUDA
        # device events are leaves and use their full totals.
        device_type_str = str(e.device_type)
        is_cpu = device_type_str.startswith("DeviceType.CPU")

        # cpu time sample (exclusive of children)
        self_cpu_time: int = e.self_cpu_time_total
        if self_cpu_time > 0:
            data_added = True
            handle.push_cputime(int(self_cpu_time / count * _NANOS_PER_MICROSECOND), count)

        # gpu time sample: exclusive for CPU operators, full device time for leaves
        gpu_time: int = getattr(e, self_gpu_time_attr) if is_cpu else getattr(e, total_gpu_time_attr)
        if gpu_time > 0:
            data_added = True
            handle.push_gpu_gputime(int(gpu_time / count * _NANOS_PER_MICROSECOND), count)

        # gpu flops sample
        flops: int = e.flops
        if flops is not None and flops > 0:
            data_added = True
            handle.push_gpu_flops(flops, count)

        # GPU memory usage: exclusive for CPU operators, full usage for leaves
        gpu_memory: int = getattr(e, self_gpu_memory_attr) if is_cpu else getattr(e, total_gpu_memory_attr)
        if gpu_memory is not None and gpu_memory > 0:
            data_added = True
            handle.push_gpu_memory(gpu_memory, count)

        if not data_added:
            if empty_events_count % 1000 == 0:
                LOG.debug("%d events with no data to record: %s", empty_events_count, e)
            empty_events_count += 1
            continue

        # Reconstruct the operator call tree by walking up the cpu_parent chain.
        # Frames are pushed leaf-first (this event), then each ancestor, so that
        # the flame graph nests children under their parents instead of rendering
        # every operator as a flat leaf. Stacks go root last.
        handle.push_frame(e.name, _FILE_PLACEHOLDER, 0, 0)
        parent = getattr(e, "cpu_parent", None)
        depth = 0
        while parent is not None and depth < config.pytorch.max_frames:
            handle.push_frame(parent.name, _FILE_PLACEHOLDER, 0, 0)
            parent = getattr(parent, "cpu_parent", None)
            depth += 1
        # Pushing pseudoframes for the device name ("device.CPU" or "device.CUDA")
        # onto the stack allows differentiation of pytorch frames from other profiling frames
        # in the flame graph. Note that stacks go root last, so this goes at the end.
        handle.push_frame(f"PYTORCH_{device_type_str}", _DEVICE_FRAME_FILE_NAME, 0, 0)
        handle.push_gpu_device_name(f"cuda {e.device_index}")

        # Get thread info from cache or compute and cache it
        thread_id = e.thread
        cache_key = (thread_id, device_type_str, e.device_index)
        if cache_key not in thread_info_cache:
            native_id = _threading.get_thread_native_id(thread_id)
            if device_type_str.startswith("DeviceType.CPU"):
                thread_name = _threading.get_thread_name(thread_id) or f"PYTORCH-CPU-THREAD-{thread_id}"
            elif device_type_str.startswith("DeviceType.CUDA"):
                thread_name = f"PYTORCH-CUDA-{e.device_index}"
            else:
                raise AttributeError(f"Unexpected device_type {device_type_str}")
            thread_info_cache[cache_key] = (native_id, thread_name)

        native_id, thread_name = thread_info_cache[cache_key]
        handle.push_threadinfo(thread_id, native_id, thread_name)

        handle.push_absolute_ns(int(trace_start_ns + e.time_range.end * _NANOS_PER_MICROSECOND))
        handle.flush_sample()
