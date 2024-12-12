from __future__ import absolute_import

import abc
import logging
import typing

import wrapt

from ddtrace._trace.tracer import Tracer
from ddtrace.internal.datadog.profiling import ddup
from ddtrace.profiling import collector
from ddtrace.profiling.recorder import Recorder


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
        super()._start_service()

    def _stop_service(self):
        # type: (...) -> None
        """Stop collecting framework profiler usage."""
        super()._stop_service()
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

    def __init__(self, recorder=None):
        super().__init__(recorder)

    def _get_patch_target(self):
        # type: (...) -> typing.Any
        return self._torch_module.profiler.profile

    def _set_patch_target(
        self, value  # type: typing.Any
    ):
        # type: (...) -> None
        self._torch_module.profiler.profile = value


def handle_torch_trace(prof):
    LOG.debug("handle_torch_trace called")
    for i in range(20):
        handle = ddup.SampleHandle()
        handle.push_gpu_gputime(10000000, 1)
        handle.push_frame("test_cuda_kernel" + str(i), "", 0, -1)
        handle.push_gpu_device_name("cuda 0")
        handle.flush_sample()
