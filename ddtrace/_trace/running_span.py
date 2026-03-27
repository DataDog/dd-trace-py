from contextlib import contextmanager
from itertools import chain
import sys
import threading
import time
from typing import Optional
from typing import Union

from ddtrace import config
from ddtrace import tracer
from ddtrace._trace.context import Context
from ddtrace._trace.span import Span
from ddtrace.constants import PARTIAL_VERSION
from ddtrace.constants import WAS_LONG_RUNNING
from ddtrace.internal import atexit
from ddtrace.internal.constants import SPAN_API_DATADOG
from ddtrace.internal.logger import get_logger
from ddtrace.internal.threads import Lock


log = get_logger(__name__)


class RunningSpanManager:
    def __init__(self) -> None:
        self._timer: Optional[threading.Timer] = None
        self._spans: dict[tuple[int, int], Span] = {}
        self._lock = Lock()
        self._is_shutting_down: bool = False

        # Register cleanup on process exit
        try:
            atexit.register(self._cleanup_on_exit)
            atexit.register_on_exit_signal(self._cleanup_on_exit)
        except BaseException:
            log.debug("SpanManager initializing during shutdown", exc_info=True)
            pass

    def _cleanup_on_exit(self) -> None:
        """Clean up all resources when the process exits."""
        with self._lock:
            self._is_shutting_down = True

            if self._timer:
                self._timer.cancel()

            # Check if we have any spans to flush before exiting
            spans_to_close = list(self._spans.values())
            self._spans.clear()

        for span in spans_to_close:
            self._finish_span(span)

    def _create_submit_timer(self) -> None:
        """This function must be called under a lock"""
        if self._is_shutting_down:
            return

        try:
            if self._timer:
                self._timer.cancel()

            timer = threading.Timer(float(config._long_running_flush_interval), self._submit_running_spans)
            timer.daemon = True
            self._timer = timer
            timer.start()
        except Exception:
            if self._timer:
                self._timer.cancel()
            self._timer = None

    def _submit_running_spans(self) -> None:
        if self._is_shutting_down:
            return

        with self._lock:
            # Timer callback raced with stop_running_span(); do not re-arm.
            if not self._spans:
                return
            self._create_submit_timer()
            running_spans = list(self._spans.values())

        for span in running_spans:
            self._emit_partial_span(span)

    def _emit_partial_span(self, span: Span) -> None:
        partial_version = time.time_ns()
        with self._lock:
            # Check both that the span is still tracked and not finished to avoid
            # race conditions with stop_running_span which removes and finishes spans.
            span_key = (span.trace_id, span.span_id)
            if span_key not in self._spans or span.finished:
                return

            if span.get_metric(PARTIAL_VERSION) is None:
                span._set_attribute(PARTIAL_VERSION, partial_version)

        partial_span = self._create_partial_span(span)
        partial_span._set_attribute(PARTIAL_VERSION, partial_version)
        partial_span.finish()

        # If the root span is a long-running span and some child spans are not,
        # they will not be sent until the root span is truly finished.
        # This block mitigates this by forcing flush of already-finished child spans.
        aggregator = tracer._span_aggregator
        finished_spans: list["Span"] = []
        with aggregator._lock:
            if span.trace_id in aggregator._traces:
                trace = aggregator._traces[span.trace_id]
                remaining_spans = []

                for s in trace.spans:
                    if s.finished and s.span_id != span.span_id:
                        finished_spans.append(s)
                    else:
                        remaining_spans.append(s)
                trace.spans[:] = remaining_spans
                trace.num_finished -= len(finished_spans)

        spans_to_write = [partial_span] + finished_spans
        try:
            spans = spans_to_write
            for tp in chain(
                aggregator.dd_processors,
                aggregator.user_processors,
                [aggregator.sampling_processor, aggregator.tags_processor, aggregator.service_name_processor],
            ):
                try:
                    spans = tp.process_trace(spans) or []
                    if not spans:
                        return
                except Exception:
                    log.error("error applying processor %r to trace %d", tp, span.trace_id, exc_info=True)
                    raise
        except Exception:
            spans = spans_to_write
        aggregator.writer.write(spans)

    def _create_partial_span(self, span: Span) -> Span:
        partial_span = Span(
            name=span.name,
            resource=span.resource,
            service=span.service,
            span_type=span.span_type,
            trace_id=span.trace_id,
            span_id=span.span_id,
            parent_id=span.parent_id,
            context=span.context,
        )
        partial_span.start_ns = span.start_ns
        partial_span._meta = span._meta.copy()
        partial_span._metrics = span._metrics.copy()
        partial_span._meta_struct = span._meta_struct.copy()
        partial_span.error = span.error
        partial_span._links = list(span._links)
        partial_span._events = list(span._events)

        return partial_span

    def _finish_span(self, span: Span):
        if span.get_metric(PARTIAL_VERSION) is not None:
            del span._metrics[PARTIAL_VERSION]
            span._set_attribute(WAS_LONG_RUNNING, 1)

        span.finish()

    def watch_span(self, span: Span):
        with self._lock:
            if not self._spans:
                self._create_submit_timer()
            self._spans[(span.trace_id, span.span_id)] = span

    def stop_running_span(self, span: Span):
        span_key = (span.trace_id, span.span_id)
        with self._lock:
            self._spans.pop(span_key, None)

            if not self._spans:
                # if there is no running span the timer should stop
                timer = self._timer
                if timer:
                    timer.cancel()
                    self._timer = None

        self._finish_span(span)


_running_span_manager: Optional[RunningSpanManager] = None
_lock = Lock()


def get_running_span_manager() -> RunningSpanManager:
    global _running_span_manager
    with _lock:
        if _running_span_manager is None:
            _running_span_manager = RunningSpanManager()
    return _running_span_manager


def start_running_span(
    name: str,
    child_of: Optional[Union[Span, Context]] = None,
    service: Optional[str] = None,
    resource: Optional[str] = None,
    span_type: Optional[str] = None,
    activate: bool = False,
    span_api: str = SPAN_API_DATADOG,
) -> Span:
    span = tracer.start_span(name, child_of, service, resource, span_type, activate, span_api)
    get_running_span_manager().watch_span(span)
    return span


def stop_running_span(span: Span) -> None:
    get_running_span_manager().stop_running_span(span)


@contextmanager
def running_span(
    name: str,
    child_of: Optional[Union[Span, Context]] = None,
    service: Optional[str] = None,
    resource: Optional[str] = None,
    span_type: Optional[str] = None,
    activate: bool = False,
    span_api: str = SPAN_API_DATADOG,
):
    span = start_running_span(name, child_of, service, resource, span_type, activate, span_api)
    try:
        yield span
    except BaseException:
        exc_type, exc_val, exc_tb = sys.exc_info()
        if exc_type is not None and exc_val is not None:
            span.set_exc_info(exc_type, exc_val, exc_tb)
        raise
    finally:
        stop_running_span(span)
