"""Simple wrapper around stack native extension module."""

import logging
import sys
from types import ModuleType
import typing

from ddtrace.internal import core
from ddtrace.internal.datadog.profiling import stack
from ddtrace.internal.settings.profiling import config
from ddtrace.profiling import collector
from ddtrace.profiling.collector import _task
from ddtrace.profiling.collector import threading
from ddtrace.trace import Tracer


LOG = logging.getLogger(__name__)


class StackCollector(collector.Collector):
    """Execution stacks collector."""

    __slots__ = (
        "nframes",
        "tracer",
        "_native_call_monitor",
    )

    def __init__(self, nframes: typing.Optional[int] = None, tracer: typing.Optional[Tracer] = None):
        super().__init__()

        self.nframes = nframes if nframes is not None else config.max_frames
        self.tracer = tracer
        self._native_call_monitor: typing.Optional[ModuleType] = None

    def __repr__(self) -> str:
        class_name = self.__class__.__name__
        attrs = {k: v for k, v in self.__dict__.items() if not k.startswith("_")}
        attrs_str = ", ".join(f"{k}={v!r}" for k, v in attrs.items())

        slot_attrs = {slot: getattr(self, slot) for slot in self.__slots__ if not slot.startswith("_")}
        slot_attrs_str = ", ".join(f"{k}={v!r}" for k, v in slot_attrs.items())

        return f"{class_name}({attrs_str}, {slot_attrs_str})"

    def _init(self) -> None:
        _task.initialize_gevent_support()

        # Start the native stack sampler first. This ensures one_time_setup() runs
        # (which handles any fork that happened since library load) before we
        # register threads and asyncio loops - otherwise those registrations would
        # be wiped out by _stack_atfork_child() in one_time_setup().
        stack.set_adaptive_sampling(config.stack.adaptive_sampling)
        stack.set_target_overhead(config.stack.adaptive_sampling_target_overhead)
        stack.set_max_sampling_period(config.stack.adaptive_sampling_max_interval)
        stack.set_max_threads(config.stack.max_threads)
        stack.set_fast_copy(config.stack.fast_copy)
        if stack.is_safe_copy_failed():
            LOG.error("No safe memory copy method available (safe_memcpy and process_vm_readv both failed).")
            raise collector.CollectorUnavailable
        if not stack.start():
            LOG.error("Failed to start the stack profiler sampling thread. CPU/wall-time profiles will be empty.")
            raise collector.CollectorUnavailable

        # Register the span-link hook only after the sampler has started successfully,
        # so we never leave a stale listener behind if startup fails.
        if self.tracer is not None:
            core.on("ddtrace.context_provider.activate", stack.link_span)

        # Start native C function call tracking (Python 3.12+ only)
        if sys.version_info >= (3, 12) and config.stack.native_frames:
            try:
                from ddtrace.internal.datadog.profiling import native_call_monitor

                native_call_monitor.start()
                self._native_call_monitor = native_call_monitor
            except Exception:
                LOG.debug("Failed to start native call monitor", exc_info=True)

        # Now patch the Threading module and register existing threads/asyncio loops.
        # TODO take the `threading` import out of here and just handle it in v2 startup
        threading.init_stack()

    def _start_service(self) -> None:
        # This is split in its own function to ease testing
        LOG.debug("Profiling StackCollector starting")
        self._init()
        LOG.debug("Profiling StackCollector started")

    def _stop_service(self) -> None:
        LOG.debug("Profiling StackCollector stopping")
        if self._native_call_monitor is not None:
            try:
                self._native_call_monitor.stop()
            except Exception:
                LOG.debug("Failed to stop native call monitor", exc_info=True)
            self._native_call_monitor = None
        if self.tracer is not None:
            core.reset_listeners("ddtrace.context_provider.activate", stack.link_span)
        LOG.debug("Profiling StackCollector stopped")

        # Tell the native thread running the v2 sampler to stop
        stack.stop()
