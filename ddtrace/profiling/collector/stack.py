"""Simple wrapper around stack native extension module."""

import logging
from types import ModuleType
import typing

from ddtrace._trace.provider import BaseContextProvider
from ddtrace.internal import core
from ddtrace.internal.compat import is_at_least_py
from ddtrace.internal.datadog.profiling import context_meta
from ddtrace.internal.datadog.profiling import stack
from ddtrace.internal.datadog.profiling._types import ProfilerTracer
from ddtrace.internal.native._native import Context
from ddtrace.internal.native._native import SpanData
from ddtrace.internal.settings.profiling import config
from ddtrace.internal.telemetry import telemetry_writer
from ddtrace.internal.telemetry.constants import TELEMETRY_LOG_LEVEL
from ddtrace.profiling import _asyncio
from ddtrace.profiling import _span_links
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
    )

    def __init__(self, nframes: typing.Optional[int] = None, tracer: typing.Optional[ProfilerTracer] = None) -> None:
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
                LOG.debug("[profiling] Failed to start native call monitor", exc_info=True)

        # Now patch the Threading module and register existing threads/asyncio loops.
        # TODO take the `threading` import out of here and just handle it in v2 startup
        threading.init_stack()

        if self.tracer is not None:
            # links_close() drops the writes still in flight from an earlier lifecycle. The listeners are registered
            # before links_open(), so every activation after it is published.
            stack.links_close()
            try:
                core.on("ddtrace.context_provider.activate", self._link_span)
                core.on("trace.span_finish", self._on_span_finish)
            except Exception:
                core.reset_listeners("ddtrace.context_provider.activate", self._link_span)
                core.reset_listeners("trace.span_finish", self._on_span_finish)
                raise
            stack.links_open()
            _span_links.mirror_lifecycle(self.tracer)
            # Publish the span that was activated on this thread before the profiler started. Other threads are
            # published on their next activation. active() may activate an unfinished ancestor, which publishes it too.
            provider = typing.cast(BaseContextProvider, self.tracer.context_provider)
            try:
                active = provider.active()
            except Exception:
                LOG.debug("[profiling] Failed to read the active span", exc_info=True)
            else:
                self._link_span(provider, active)

    def _link_span(
        self,
        provider: BaseContextProvider,
        span: typing.Optional[typing.Union[Context, SpanData]],
    ) -> None:
        if self.tracer is None or provider is not self.tracer.context_provider:
            return
        try:
            token = stack.lifecycle()
            # The event can carry a span that already finished, for example when a span that finished meanwhile is
            # restored as the active one. Publish what active() would return instead.
            item = context_meta.resolve(span) if context_meta.inheritance_mode(provider) else span
            task_id = _asyncio.current_task_link_id()
            greenlet_id = _task.current_tracked_greenlet_id()
            _span_links.publish(provider, token, True, task_id, greenlet_id, item)
        except Exception:  # nosec B110
            pass

    def _on_span_finish(self, span: SpanData) -> None:
        if self.tracer is None:
            return
        try:
            token = stack.lifecycle()
            if stack.holds_span(span.span_id):
                _span_links.retarget_finished(self.tracer.context_provider, token, span)
        except Exception:  # nosec B110
            pass

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
        LOG.debug("[profiling] StackCollector starting")
        self._init()
        LOG.debug("[profiling] StackCollector started")

    def _stop_service(self) -> None:
        LOG.debug("[profiling] StackCollector stopping")
        if self.tracer is not None:
            # Close first, so that writers in flight and listeners still running are dropped.
            stack.links_close()
            _span_links.mirror_lifecycle(None)
        if self._native_call_monitor is not None:
            try:
                self._native_call_monitor.stop()
            except Exception:
                LOG.debug("[profiling] Failed to stop native call monitor", exc_info=True)
            self._native_call_monitor = None
        if self.tracer is not None:
            core.reset_listeners("ddtrace.context_provider.activate", self._link_span)
            core.reset_listeners("trace.span_finish", self._on_span_finish)
        LOG.debug("[profiling] StackCollector stopped")

        # Tell the native thread running the v2 sampler to stop
        stack.stop()
