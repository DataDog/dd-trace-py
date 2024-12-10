from __future__ import absolute_import

import abc
import logging
import typing

import attr
import wrapt

from ddtrace._trace.tracer import Tracer
from ddtrace.internal.datadog.profiling import ddup
from ddtrace.profiling import _threading
from ddtrace.profiling import collector
from ddtrace.profiling.recorder import Recorder
from ddtrace.settings.profiling import config


LOG = logging.getLogger(__name__)

pytorch_events_limit = config.pytorch.events_limit or 1_000_000


class _WrappedTorchProfiler(wrapt.ObjectProxy):
    def __init__(
        self,
        wrapped: typing.Any,
        recorder: Recorder,
        tracer: typing.Optional[Tracer],
    ) -> None:
        wrapt.ObjectProxy.__init__(self, wrapped)
        self.on_trace_ready = handle_torch_trace
        self._self_recorder = recorder
        self._self_tracer = tracer


@attr.s
class MLProfilerCollector(collector.CaptureSamplerCollector):
    """Record ML framework (i.e. pytorch) profiler usage."""

    tracer = attr.ib(default=None)
    _original = attr.ib(init=False, repr=False, type=typing.Any, cmp=False)

    @abc.abstractmethod
    def _get_original(self):
        # type: (...) -> typing.Any
        pass

    @abc.abstractmethod
    def _set_original(
        self,
        value,  # type: typing.Any
    ):
        # type: (...) -> None
        pass

    def _start_service(self):
        # type: (...) -> None
        """Start collecting framework profiler usage."""
        try:
            import torch
        except ImportError as e:
            raise collector.CollectorUnavailable(e)
        self._torch_module = torch
        self.patch()
        super(MLProfilerCollector, self)._start_service()

    def _stop_service(self):
        # type: (...) -> None
        """Stop collecting framework profiler usage."""
        super(MLProfilerCollector, self)._stop_service()
        self.unpatch()

    def patch(self):
        # type: (...) -> None
        """Patch the module for tracking profiling data."""
        # We only patch the profile call from the `torch.profiler` module.
        self.original = self._get_original()

        def profiler_init(wrapped, instance, args, kwargs):
            profiler = wrapped(*args, **kwargs)
            return self.PROFILED_TORCH_CLASS(
                profiler,
                self.recorder,
                self.tracer,
            )

        self._set_original(wrapt.FunctionWrapper(self.original, profiler_init))

    def unpatch(self):
        # type: (...) -> None
        """Unpatch the torch.profiler module for tracking profiling data."""
        self._set_original(self.original)


@attr.s
class TorchProfilerCollector(MLProfilerCollector):
    """Monkey patch torch.profiler.profile usage."""

    PROFILED_TORCH_CLASS = _WrappedTorchProfiler

    def _get_original(self):
        # type: (...) -> typing.Any
        return self._torch_module.profiler.profile

    def _set_original(
        self, value  # type: typing.Any
    ):
        # type: (...) -> None
        self._torch_module.profiler.profile = value


def handle_torch_trace(prof):
    NANOS_PER_MICROSECOND = 1e3
    LOG.debug("handle_torch_trace called")
    # need an upper bound of events collected, can be adjusted based on profile size
    num_events_collected = min(len(prof.events()), pytorch_events_limit)
    trace_start_us = prof.profiler.kineto_results.trace_start_us()
    for e in prof.events()[:num_events_collected]:
        device_name = "cuda " + str(e.device_index)

        if str(e.device_type).startswith("DeviceType.CUDA"):
            # gpu time sample
            end_time_us = int(trace_start_us + e.time_range.end)
            event_duration_us = e.time_range.elapsed_us()
            handle = ddup.SampleHandle()
            handle.push_gpu_gputime(int(event_duration_us * NANOS_PER_MICROSECOND), 1)
            handle.push_gpu_device_name(device_name)
            handle.push_monotonic_ns(int(end_time_us * NANOS_PER_MICROSECOND))
            handle.push_threadinfo(
                e.thread, _threading.get_thread_native_id(e.thread), _threading.get_thread_name(e.thread)
            )
            handle.push_frame(e.name, "", 0, -1)
            handle.flush_sample()

        if e.flops is not None and e.flops > 0:
            # gpu flops sample
            handle = ddup.SampleHandle()
            handle.push_gpu_flops(e.flops, 1)
            handle.push_gpu_device_name(device_name)
            handle.push_frame(e.name, "", 0, -1)
            handle.flush_sample()

        if e.flops is not None and e.cuda_memory_usage > 0:
            # gpu mem sample
            handle = ddup.SampleHandle()
            handle.push_gpu_memory(e.cuda_memory_usage, 1)
            handle.push_gpu_device_name(device_name)
            handle.push_frame(e.name, "", 0, -1)
            handle.flush_sample()
