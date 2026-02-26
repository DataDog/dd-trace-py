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

        def profiler_init(wrapped, instance, args, kwargs):
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
    NANOS_PER_MICROSECOND = 1e3
    LOG.debug("_handle_torch_trace called")
    events = prof.events()
    if len(events) == 0:
        return

    # need an upper bound of events collected, can be adjusted based on profile size.
    # There is no way for us to ask the PyTorch profiler to limit the num of samples
    # (it is adjusted through the Schedule set by the user).
    # We truncate to keep the uploaded profile to a reasonable size.
    # For now, experiment with a default of 1_000_000 if nothing is set.
    # TODO, better values here.
    collection_fraction = 1.0
    num_events_to_report = min(len(events), config.pytorch.events_limit or 1_000_000)
    if num_events_to_report < len(events):
        LOG.debug("Dropped events.  num_events_to_report %d. len(events): %d", num_events_to_report, len(events))
        collection_fraction = num_events_to_report / len(events)

    empty_events_count = 0

    # earlier versions use microsecond, later versions use nanosecond
    kineto_results = prof.profiler.kineto_results
    if hasattr(kineto_results, "trace_start_ns"):
        trace_start_ns = kineto_results.trace_start_ns()
    elif hasattr(kineto_results, "trace_start_us"):
        trace_start_ns = kineto_results.trace_start_us() * NANOS_PER_MICROSECOND
    else:
        raise AttributeError("Neither trace_start_ns nor trace_start_us exists")

    for e in events:
        if collection_fraction < random.random():  # nosec: used for sampling, not security
            continue

        handle = ddup.SampleHandle()
        data_added = False

        # cpu time sample
        if e.cpu_time > 0:
            data_added = True
            handle.push_cputime(int(e.cpu_time * NANOS_PER_MICROSECOND), e.count)

        # gpu time sample - both device_time and cuda_time are in microseconds
        # device_time is the modern API; cuda_time is deprecated and only used on older PyTorch
        gpu_time = getattr(e, "device_time", None) or getattr(e, "cuda_time", None)
        if gpu_time is not None and gpu_time > 0:
            data_added = True
            handle.push_gpu_gputime(int(gpu_time * NANOS_PER_MICROSECOND), e.count)

        # gpu flops sample
        if e.flops is not None and e.flops > 0:
            data_added = True
            handle.push_gpu_flops(e.flops, e.count)

        # GPU memory usage
        # device_memory_usage is the modern API; cuda_memory_usage is deprecated
        gpu_memory = getattr(e, "device_memory_usage", None) or getattr(e, "cuda_memory_usage", None)
        if gpu_memory is not None and gpu_memory > 0:
            data_added = True
            handle.push_gpu_memory(gpu_memory, e.count)

        # If there is data, flush it to the profile.
        # Otherwise, do nothing and the sample object will be dropped when it goes out of scope
        if data_added:
            handle.push_frame(e.name, "unknown-file", 0, 0)
            # Pushing pseudoframes for the device name ("device.CPU" or "device.CUDA")
            # onto the stack allows differentation of pytorch frames from other profiling frames
            # in the flame graph.
            # Note that stacks go root last, so this goes at the end
            handle.push_frame("PYTORCH_" + str(e.device_type), "unknown-file", 0, 0)

            handle.push_gpu_device_name("cuda " + str(e.device_index))

            if str(e.device_type).startswith("DeviceType.CPU"):
                # There is a known issue with getting thread ids and names from pytorch.
                # If we can't get one, just use a default name.
                handle.push_threadinfo(
                    e.thread,
                    _threading.get_thread_native_id(e.thread),
                    _threading.get_thread_name(e.thread) or "PYTORCH-CPU-THREAD-" + str(e.thread),
                )
            elif str(e.device_type).startswith("DeviceType.CUDA"):
                handle.push_threadinfo(
                    e.thread, _threading.get_thread_native_id(e.thread), "PYTORCH-CUDA-" + str(e.device_index)
                )
            else:
                raise AttributeError(f"Unexpected device_type {e.device_type}")

            handle.push_absolute_ns(int(trace_start_ns + e.time_range.end * NANOS_PER_MICROSECOND))
            handle.flush_sample()
        else:
            if empty_events_count % 1000 == 0:
                LOG.debug("%d events with no data to record: %s", empty_events_count, e)
            empty_events_count += 1
