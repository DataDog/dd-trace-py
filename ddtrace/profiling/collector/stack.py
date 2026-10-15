"""Simple wrapper around stack native extension module."""

import logging
from types import ModuleType
import typing

from ddtrace.internal import core
from ddtrace.internal.compat import is_at_least_py
from ddtrace.internal.datadog.profiling import stack
from ddtrace.internal.datadog.profiling._types import ProfilerTracer
from ddtrace.internal.settings.profiling import config
from ddtrace.internal.span_lifecycle import SpanActivationSubscription
from ddtrace.internal.span_lifecycle import SpanCompletedEvent
from ddtrace.internal.span_lifecycle import SpanIdentity
from ddtrace.internal.telemetry import telemetry_writer
from ddtrace.internal.telemetry.constants import TELEMETRY_LOG_LEVEL
from ddtrace.profiling import collector
from ddtrace.profiling.collector import _task
from ddtrace.profiling.collector import threading


LOG = logging.getLogger(__name__)


class StackCollector(collector.Collector):
    """Execution stacks collector."""

    __slots__ = (
        "nframes",
        "tracer",
        "_native_call_monitor",
        "_activation",
    )

    def __init__(self, nframes: typing.Optional[int] = None, tracer: typing.Optional[ProfilerTracer] = None) -> None:
        super().__init__()

        self.nframes = nframes if nframes is not None else config.max_frames
        self.tracer = tracer
        self._activation = (
            SpanActivationSubscription(lambda: tracer.context_provider, self._link_span) if tracer else None
        )
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

        # Import _faulthandler BEFORE starting the sampler. This ensures that if
        # faulthandler.enable was already called (e.g., by pytest), we reinstall
        # our SIGSEGV handler before sampling begins. Our handler chains to
        # faulthandler's for non-recovery faults.
        from ddtrace.profiling import _faulthandler  # noqa: F401

        # Start the native stack sampler first. This ensures one_time_setup() runs
        # (which handles any fork that happened since library load) before we
        # register threads and asyncio loops - otherwise those registrations would
        # be wiped out by _stack_atfork_child() in one_time_setup().
        stack.set_adaptive_sampling(config.stack.adaptive_sampling)
        stack.set_target_overhead(config.stack.adaptive_sampling_target_overhead)
        stack.set_max_sampling_period(config.stack.adaptive_sampling_max_interval)
        stack.set_adaptive_sampling_baseline(config.stack.adaptive_sampling_baseline)
        stack.set_p_stable_window_s(config.stack.adaptive_sampling_p_stable_window_s)
        stack.set_p_stable_percentile(config.stack.adaptive_sampling_p_stable_percentile)
        stack.set_max_threads(config.stack.max_threads)
        stack.set_max_frames(self.nframes)
        stack.set_max_tasks(config.stack.max_tasks)
        stack.set_gc_enabled(config.stack.gc_enabled)
        stack.set_fast_copy(config.stack.fast_copy)
        if stack.is_safe_copy_failed():
            LOG.error("No safe memory copy method available (safe_memcpy and process_vm_readv both failed).")
            raise collector.CollectorUnavailable
        if not stack.start():
            LOG.error("Failed to start the stack profiler sampling thread. CPU/wall-time profiles will be empty.")
            raise collector.CollectorUnavailable

        # Start native C function call tracking (Python 3.12+ only)
        if is_at_least_py(3, 12) and config.stack.native_frames:
            try:
                from ddtrace.internal.datadog.profiling import native_call_monitor

                native_call_monitor.start()
                self._native_call_monitor = native_call_monitor
            except Exception:
                LOG.debug("Failed to start native call monitor", exc_info=True)

        # Now patch the Threading module and register existing threads/asyncio loops.
        # TODO take the `threading` import out of here and just handle it in v2 startup
        threading.init_stack()

        # Register only after every fallible initialization step. A failed collector is dropped without
        # _stop_service(), so registering earlier could leave process-wide tracing listeners behind.
        if self._activation is not None:
            try:
                self._activation.start()
                core.on(SpanCompletedEvent.event_name, self._unlink_finished_span)
            except Exception:
                self._activation.close()
                core.reset_listeners(SpanCompletedEvent.event_name, self._unlink_finished_span)
                raise

    def _unlink_finished_span(self, event: SpanCompletedEvent) -> None:
        if self.tracer is not None and event.span.source_id == id(self.tracer):
            stack.unlink_finished_span(event.span.span_id)

    def _link_span(self, identity: typing.Optional[SpanIdentity]) -> None:
        if identity is None:
            stack.clear_span()
        else:
            stack.link_span(identity.span_id, identity.correlation_root_span_id, identity.correlation_root_span_type)

    @staticmethod
    def snapshot() -> None:
        # The sampling thread cannot touch Python, so it stashes the exception that killed
        # it and we drain it here, on the scheduler thread, before every upload.
        error = stack.take_sampling_thread_error()
        if error is None:
            return

        error_type, message = error
        LOG.error(
            "The stack profiler sampling thread stopped after an unexpected error: %s: %s",
            error_type,
            message,
            # The message is reported below with the error type as a tag, so the telemetry
            # payload stays low cardinality.
            extra={"send_to_telemetry": False},
        )
        telemetry_writer.add_log(
            TELEMETRY_LOG_LEVEL.ERROR,
            "The stack profiler sampling thread stopped after an unexpected error",
            tags={"error_type": error_type},
        )

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
        if self._activation is not None:
            self._activation.close()
            core.reset_listeners(SpanCompletedEvent.event_name, self._unlink_finished_span)
        LOG.debug("Profiling StackCollector stopped")

        # Tell the native thread running the v2 sampler to stop
        stack.stop()
