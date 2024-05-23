from __future__ import absolute_import
import logging
import abc
import os.path
import sys
import typing
import attr
import torch

from ddtrace._trace.tracer import Tracer
from ddtrace.internal import compat
from ddtrace.internal.datadog.profiling import ddup
from ddtrace.profiling import _threading
from ddtrace.profiling import collector
from ddtrace.profiling.recorder import Recorder
from ddtrace.settings.profiling import config
from ddtrace.vendor import wrapt
from ddtrace.internal.datadog.profiling import ddup


LOG = logging.getLogger(__name__)

# We need to know if wrapt is compiled in C or not. If it's not using the C module, then the wrappers function will
# appear in the stack trace and we need to hide it.
if os.environ.get("WRAPT_DISABLE_EXTENSIONS"):
    WRAPT_C_EXT = False
else:
    try:
        import ddtrace.vendor.wrapt._wrappers as _w  # noqa: F401
    except ImportError:
        WRAPT_C_EXT = False
    else:
        WRAPT_C_EXT = True
        del _w


class _WrappedTorchProfiler(wrapt.ObjectProxy):
    def __init__(
        self,
        wrapped: typing.Any,
        recorder: Recorder,
        tracer: typing.Optional[Tracer],
        max_nframes: int,
        capture_sampler: collector.CaptureSampler,
        endpoint_collection_enabled: bool,
        export_libdd_enabled: bool,
    ) -> None:
        wrapt.ObjectProxy.__init__(self, wrapped)
        LOG.warn("INIT MONKEY PATCHED PYTORCH PROFILE CALL!!")
        LOG.warn(wrapped)
        LOG.warn(self)
        self.on_trace_ready = handle_torch_trace
        self._self_recorder = recorder
        self._self_tracer = tracer
        self._self_max_nframes = max_nframes
        self._self_capture_sampler = capture_sampler
        self._self_endpoint_collection_enabled = endpoint_collection_enabled
        self._self_export_libdd_enabled = export_libdd_enabled
        frame = sys._getframe(2 if WRAPT_C_EXT else 3)
        code = frame.f_code
        self._self_name = "%s:%d" % (os.path.basename(code.co_filename), frame.f_lineno)


class FunctionWrapper(wrapt.FunctionWrapper):
    # Override the __get__ method: whatever happens, _allocate_lock is always considered by Python like a "static"
    # method, even when used as a class attribute. Python never tried to "bind" it to a method, because it sees it is a
    # builtin function. Override default wrapt behavior here that tries to detect bound method.
    def __get__(self):
        return self


@attr.s
class MLProfilerCollector(collector.CaptureSamplerCollector):
    """Record lock usage."""

    nframes = attr.ib(type=int, default=config.max_frames)
    endpoint_collection_enabled = attr.ib(type=bool, default=config.endpoint_collection)
    export_libdd_enabled = attr.ib(type=bool, default=config.export.libdd_enabled)

    tracer = attr.ib(default=None)

    _original = attr.ib(init=False, repr=False, type=typing.Any, cmp=False)

    # Check if libdd is available, if not, disable the feature
    if export_libdd_enabled and not ddup.is_available:
        export_libdd_enabled = False

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
        """Start collecting lock usage."""
        self.patch()
        super(MLProfilerCollector, self)._start_service()

    def _stop_service(self):
        # type: (...) -> None
        """Stop collecting lock usage."""
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
                self.nframes,
                self._capture_sampler,
                self.endpoint_collection_enabled,
                self.export_libdd_enabled,
            )

        self._set_original(FunctionWrapper(self.original, profiler_init))

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
        return torch.profiler.profile

    def _set_original(
        self, value  # type: typing.Any
    ):
        # type: (...) -> None
        torch.profiler.profile = value  # type: ignore[misc]


def handle_torch_trace(prof):
    NANOS_PER_MICROSECOND = 1e3
    LOG.warn("handle torch trace was called")
    trace_start_us = prof.profiler.kineto_results.trace_start_us()
    for i, e in enumerate(prof.events()):
        device_name = "cuda " + str(e.device_index)
        end_time_us = int(trace_start_us + e.time_range.end)
        event_duration_us = e.time_range.elapsed_us()
        if str(e.device_type).startswith("DeviceType.CUDA") and i % 10 == 0:
            # gpu time sample
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
