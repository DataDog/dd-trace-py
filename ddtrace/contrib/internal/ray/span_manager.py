import atexit
from contextlib import contextmanager
from itertools import chain
import threading
import time
from typing import Dict
from typing import Optional
from typing import Tuple
from typing import Union

from ray.dashboard.modules.job.common import JobInfo

from ddtrace import config
from ddtrace import tracer
from ddtrace._trace.context import Context
from ddtrace._trace.span import Span
from ddtrace.constants import ERROR_MSG
from ddtrace.constants import SPAN_KIND
from ddtrace.ext import SpanKind
from ddtrace.internal.forksafe import Lock
from ddtrace.internal.logger import get_logger

from .constants import DD_PARTIAL_VERSION
from .constants import DD_WAS_LONG_RUNNING
from .constants import RAY_COMPONENT
from .constants import RAY_JOB_MESSAGE
from .constants import RAY_JOB_STATUS
from .constants import RAY_STATUS_FAILED
from .constants import RAY_STATUS_FINISHED
from .constants import RAY_STATUS_RUNNING
from .constants import RAY_SUBMISSION_ID_TAG
from .utils import _inject_ray_span_tags_and_metrics


log = get_logger(__name__)


@contextmanager
def long_running_ray_span(
    span_name: str,
    service: str,
    span_type: str,
    resource: Optional[str] = None,
    child_of: Optional[Union[Span, Context]] = None,
    activate: bool = True,
):
    """Context manager that handles Ray span creation and long-running span lifecycle"""
    with tracer.start_span(
        name=span_name, service=service, resource=resource, span_type=span_type, child_of=child_of, activate=activate
    ) as span:
        span.set_tag_str(SPAN_KIND, SpanKind.CONSUMER)
        _inject_ray_span_tags_and_metrics(span)
        start_long_running_span(span)

        try:
            yield span
        except BaseException as e:
            raise e
        finally:
            stop_long_running_span(span)


class RaySpanManager:
    def __init__(self) -> None:
        self._timers: Dict[str, threading.Timer] = {}
        # {submission_id: {(trace_id, span_id): Span}}
        self._job_spans: Dict[str, Dict[Tuple[int, int], Span]] = {}
        # {submission_id: (Span)}
        self._root_spans: Dict[str, Span] = {}
        self._lock = Lock()
        self._is_shutting_down: bool = False

        # Register cleanup on process exit
        try:
            atexit.register(self.cleanup_on_exit)
        except BaseException:
            log.debug("SpanManager initializing during shutdown", exc_info=True)
            pass

    def _get_submission_id(self, span: Span) -> Optional[str]:
        return span.get_tag(RAY_SUBMISSION_ID_TAG)

    def cleanup_on_exit(self) -> None:
        """Clean up all resources when the process exits."""
        with self._lock:
            self._is_shutting_down = True

            for _, timer in list(self._timers.items()):
                timer.cancel()

            # Check if we have any spans to flush before exiting
            spans_to_close = []
            for spans_dict in self._job_spans.values():
                spans_to_close.extend(list(spans_dict.values()))

            self._timers.clear()
            self._job_spans.clear()
            self._root_spans.clear()

        for span in spans_to_close:
            self._finish_span(span)

    def _emit_partial_span(self, span: Span) -> None:
        partial_version = time.time_ns()
        if span.get_metric(DD_PARTIAL_VERSION) is None:
            span.set_metric(DD_PARTIAL_VERSION, partial_version)
            span.set_tag_str(RAY_JOB_STATUS, RAY_STATUS_RUNNING)

        partial_span = self._recreate_job_span(span)
        partial_span.set_tag_str(RAY_JOB_STATUS, RAY_STATUS_RUNNING)
        partial_span.set_metric(DD_PARTIAL_VERSION, partial_version)
        partial_span.finish()

        # Sending spans which are waiting for long running spans to finish
        aggregator = tracer._span_aggregator
        finished_spans = []
        with aggregator._lock:
            if span.trace_id in aggregator._traces:
                trace = aggregator._traces[span.trace_id]
                finished_spans = []
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
                spans = tp.process_trace(spans) or []
                if not spans:
                    return
        except Exception:
            spans = spans_to_write
        aggregator.writer.write(spans)

    def _create_resubmit_timer(self, submission_id: str, time: float) -> None:
        """This function should be called under a lock"""
        if self._is_shutting_down:
            return

        try:
            timer = threading.Timer(time, self._resubmit_long_running_spans, args=[submission_id])
            timer.daemon = True
            self._timers[submission_id] = timer
            timer.start()
        except Exception:
            self._timers.pop(submission_id, None)

    def _recreate_job_span(self, job_span: Span) -> Span:
        new_span = Span(
            name=job_span.name,
            resource=job_span.resource,
            service=job_span.service,
            span_type=job_span.span_type,
            trace_id=job_span.trace_id,
            span_id=job_span.span_id,
            parent_id=job_span.parent_id,
            context=job_span.context,
        )
        new_span.set_tag_str("component", RAY_COMPONENT)
        new_span.start_ns = job_span.start_ns
        new_span._meta = job_span._meta.copy()
        new_span._metrics = job_span._metrics.copy()

        return new_span

    def _resubmit_long_running_spans(self, submission_id: str) -> None:
        if self._is_shutting_down:
            return

        with self._lock:
            if submission_id not in self._job_spans:
                return
            self._create_resubmit_timer(submission_id, float(config._long_running_flush_interval))
            job_spans = list(self._job_spans[submission_id].values())

        for span in job_spans:
            self._emit_partial_span(span)

    def _finish_span(self, span: Span, job_info: Optional[JobInfo] = None) -> None:
        # only if span was long running
        if span.get_metric(DD_PARTIAL_VERSION) is not None:
            del span._metrics[DD_PARTIAL_VERSION]

            span.set_metric(DD_WAS_LONG_RUNNING, 1)
            span.set_tag_str(RAY_JOB_STATUS, RAY_STATUS_FINISHED)

        if job_info:
            span.set_tag_str(RAY_JOB_STATUS, job_info.status)
            span.set_tag(RAY_JOB_MESSAGE, job_info.message)

            if str(job_info.status) == RAY_STATUS_FAILED:
                span.error = 1
                span.set_tag(ERROR_MSG, job_info.message)
        span.finish()

    def add_span(self, span: Span) -> None:
        submission_id = self._get_submission_id(span)

        with self._lock:
            if submission_id not in self._job_spans:
                self._job_spans[submission_id] = {}
                # the first timer will be only 10 seconds to have a well formed trace
                self._create_resubmit_timer(submission_id, float(config._long_running_initial_flush_interval))
            self._job_spans[submission_id][(span.trace_id, span.span_id)] = span

    def stop_long_running_span(self, span_to_stop: Span) -> None:
        self._finish_span(span_to_stop)

        submission_id = self._get_submission_id(span_to_stop)
        span_key = (span_to_stop.trace_id, span_to_stop.span_id)

        with self._lock:
            job_spans = self._job_spans.get(submission_id)
            if not job_spans:
                return

            job_spans.pop(span_key, None)
            if job_spans:
                return

            # this code will be executed if job_spans[submission_id] is empty
            timer = self._timers.pop(submission_id, None)
            if timer:
                timer.cancel()
            self._job_spans.pop(submission_id, None)

    def stop_long_running_job(self, submission_id: str, job_info: Optional[JobInfo]) -> None:
        with self._lock:
            job_span = self._root_spans[submission_id]

            timer = self._timers.pop(submission_id, None)
            if timer:
                timer.cancel()

            del self._job_spans[submission_id]
            del self._root_spans[submission_id]

        self._finish_span(job_span, job_info=job_info)


_ray_span_manager: Optional[RaySpanManager] = None


def get_span_manager() -> RaySpanManager:
    global _ray_span_manager
    if _ray_span_manager is None:
        _ray_span_manager = RaySpanManager()
    return _ray_span_manager


def start_long_running_job(job_span: Span) -> None:
    manager = get_span_manager()
    submission_id = manager._get_submission_id(job_span)

    with manager._lock:
        manager._root_spans[submission_id] = job_span

    start_long_running_span(job_span)


def stop_long_running_job(submission_id: str, job_info: Optional[JobInfo] = None) -> None:
    get_span_manager().stop_long_running_job(submission_id, job_info)


def start_long_running_span(span: Span) -> None:
    get_span_manager().add_span(span)


def stop_long_running_span(span: Span) -> None:
    get_span_manager().stop_long_running_span(span)
