from __future__ import absolute_import

import abc
import logging
import typing

import wrapt

from ddtrace._trace.tracer import Tracer
from ddtrace.internal.datadog.profiling import ddup
from ddtrace.profiling import _threading
from ddtrace.profiling import collector
from ddtrace.profiling.recorder import Recorder
from ddtrace.settings.profiling import config


LOG = logging.getLogger(__name__)


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


class MLProfilerCollector(collector.CaptureSamplerCollector):
    """Record ML framework (i.e. pytorch) profiler usage."""

    def __init__(self, recorder=None):
        super().__init__(recorder)
        self.tracer = None
        # Holds the pytorch profiler object which is wrapped by this class
        self._original: typing.Any = None

    @abc.abstractmethod
    def _get_patch_target(self):
        # type: (...) -> typing.Any
        pass

    @abc.abstractmethod
    def _set_patch_target(
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
        self._original = self._get_patch_target()

        def profiler_init(wrapped, instance, args, kwargs):
            profiler = wrapped(*args, **kwargs)
            return self.PROFILED_TORCH_CLASS(
                profiler,
                self.recorder,
                self.tracer,
            )

        self._set_patch_target(wrapt.FunctionWrapper(self._original, profiler_init))

    def unpatch(self):
        # type: (...) -> None
        """Unpatch the torch.profiler module for tracking profiling data."""
        self._set_patch_target(self._original)


class TorchProfilerCollector(MLProfilerCollector):
    """Monkey patch torch.profiler.profile usage."""

    PROFILED_TORCH_CLASS = _WrappedTorchProfiler

    def _get_patch_target(self):
        # type: (...) -> typing.Any
        return self._torch_module.profiler.profile

    def _set_patch_target(
        self, value  # type: typing.Any
    ):
        # type: (...) -> None
        self._torch_module.profiler.profile = value


def handle_torch_trace(prof):
    NANOS_PER_MICROSECOND = 1e3
    LOG.debug("handle_torch_trace called")
    # need an upper bound of events collected, can be adjusted based on profile size.
    # Sadly, there is no way AFAICT to tell the PyTorch profiler itself to limit the num of samples.
    # We truncate to keep the uploaded profile to a reasonable size.
    # For now, experiment with a default of 1_000_000 if nothing is set.
    # TODO, better values here.
    num_events_collected = min(len(prof.events()), config.pytorch.events_limit or 1_000_000)
    if num_events_collected < len(prof.events()):
        LOG.debug(
            "Dropped events",
            extra={"num_events_collected": num_events_collected, "len(prof.events())": len(prof.events())},
        )

    trace_start_us = prof.profiler.kineto_results.trace_start_us()
    for e in prof.events()[:num_events_collected]:
        handle = ddup.SampleHandle()
        data_added = False

        # gpu time sample
        if str(e.device_type).startswith("DeviceType.CUDA"):
            data_added = True
            handle.push_gpu_gputime(int(e.time_range.elapsed_us() * NANOS_PER_MICROSECOND), 1)
            # TODO, is this common data or GPU Time only?
            handle.push_threadinfo(
                e.thread, _threading.get_thread_native_id(e.thread), _threading.get_thread_name(e.thread)
            )

        # gpu flops sample
        if e.flops is not None and e.flops > 0:
            data_added = True
            handle.push_gpu_flops(e.flops, 1)

        # gpu mem sample
        if e.cuda_memory_usage is not None and e.cuda_memory_usage > 0:
            data_added = True
            handle.push_gpu_memory(e.cuda_memory_usage, 1)

        # If there is data, flush it to the profile.
        # Otherwise, do nothing and the sample object will be dropped when it goes out of scope
        if data_added:
            handle.push_frame(e.name, "", 0, -1)
            handle.push_gpu_device_name("cuda " + str(e.device_index))
            handle.push_monotonic_ns(int((trace_start_us + e.time_range.end) * NANOS_PER_MICROSECOND))
            handle.flush_sample()
        else:
            LOG.debug("event with no data to record", extra={"event": e})
