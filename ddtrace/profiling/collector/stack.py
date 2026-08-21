"""Simple wrapper around stack native extension module."""

import logging
import sys
from types import ModuleType
import typing

from ddtrace._trace.context import Context
from ddtrace._trace.provider import BaseContextProvider
from ddtrace._trace.span import Span
from ddtrace.internal import core
from ddtrace.internal.datadog.profiling import context_meta
from ddtrace.internal.datadog.profiling import stack
from ddtrace.internal.settings.profiling import config
from ddtrace.internal.telemetry import telemetry_writer
from ddtrace.internal.telemetry.constants import TELEMETRY_LOG_LEVEL
from ddtrace.profiling import collector
from ddtrace.profiling.collector import _task
from ddtrace.profiling.collector import threading
from ddtrace.trace import Tracer


LOG = logging.getLogger(__name__)


def _unlink_finished_span(span: Span) -> None:
    """Remove physical-thread attribution derived from a finished span."""
    stack.unlink_finished_span(span.span_id)


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
        stack.set_max_tasks(config.stack.max_tasks)
        stack.set_fast_copy(config.stack.fast_copy)
        if stack.is_safe_copy_failed():
            LOG.error("No safe memory copy method available (safe_memcpy and process_vm_readv both failed).")
            raise collector.CollectorUnavailable
        if not stack.start():
            LOG.error("Failed to start the stack profiler sampling thread. CPU/wall-time profiles will be empty.")
            raise collector.CollectorUnavailable

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

        # Register only after every fallible initialization step so rollback can remove every listener.
        if self.tracer is not None:
            try:
                core.on("ddtrace.context_provider.activate", self._link_span)
                core.on("trace.span_finish", _unlink_finished_span)
            except Exception:
                core.reset_listeners("ddtrace.context_provider.activate", self._link_span)
                core.reset_listeners("trace.span_finish", _unlink_finished_span)
                raise

    def _link_span(
        self,
        provider: BaseContextProvider,
        span: typing.Optional[typing.Union[Context, Span]],
    ) -> None:
        if self.tracer is None or provider is not self.tracer.context_provider:
            return
        if isinstance(span, Span):
            span_id = span.span_id
            # A Span whose _parent is None but parent_id is set was created with child_of=Context. Its local root is
            # the new span, so read the distributed local-root metadata directly from the parent Context. This works
            # across both thread and greenlet context propagation without relying on physical-thread-local state.
            if span._parent is None and span.parent_id is not None and span._parent_context is not None:
                propagated_root_span_id, propagated_root_span_type = context_meta.read_profiler_link(
                    span._parent_context
                )
                local_root_span_id = propagated_root_span_id or span._local_root.span_id
                local_root_span_type = propagated_root_span_type or span._local_root.span_type
            else:
                local_root_span_id = span._local_root.span_id
                local_root_span_type = span._local_root.span_type
            stack.link_span(span_id, local_root_span_id, local_root_span_type)
        elif isinstance(span, Context) and span.span_id is not None:
            local_root_span_id, span_type = context_meta.read_profiler_link(span)
            stack.link_span(span.span_id, local_root_span_id, span_type)
        else:
            stack.clear_span()

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
        if self.tracer is not None:
            core.reset_listeners("ddtrace.context_provider.activate", self._link_span)
            core.reset_listeners("trace.span_finish", _unlink_finished_span)
        LOG.debug("Profiling StackCollector stopped")

        # Tell the native thread running the v2 sampler to stop
        stack.stop()
