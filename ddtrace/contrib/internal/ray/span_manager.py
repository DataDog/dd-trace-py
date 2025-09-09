import threading
import time

from ddtrace import config
from ddtrace import tracer
from ddtrace._trace.span import Span
from contextlib import contextmanager
from ddtrace.constants import SPAN_KIND
from ddtrace.ext import SpanKind
from .utils import _inject_ray_span_tags


@contextmanager
def long_running_ray_span(span_name, service, span_type, child_of=None, activate=True):
    """Context manager that handles Ray span creation and long-running span lifecycle"""
    span = tracer.start_span(
        name=span_name,
        service=service,
        span_type=span_type,
        child_of=child_of,
        activate=activate
    )
    span.set_tag_str(SPAN_KIND, SpanKind.CONSUMER)
    _inject_ray_span_tags(span)
    start_long_running_span(span)

    try:
        yield span
    except Exception as e:
        span.set_exc_info(type(e), e, e.__traceback__)
        raise
    finally:
        stop_long_running_span(span)


class LongRunningJobManager:
    def __init__(self):
        self._timers = {}
        # {submission_id: {(trace_id, span_id): Span}}
        self._job_spans = {}
        # {submission_id: Span}
        self._root_spans = {}
        self._lock = threading.Lock()

    def _get_submission_id(self, span):
        submission_id = span.get_tag("ray.submission_id")
        return submission_id if submission_id else None

    def _emit_partial_span(self, span):
        partial_span = self._recreate_job_span(span)
        partial_span.set_metric("_dd.partial_version", time.time_ns())
        partial_span.finish()

        # Sending spans which are waiting for long running spans to finish
        aggregator = tracer._span_aggregator
        finished_children = []
        with aggregator._lock:
            if span.trace_id in aggregator._traces:
                trace = aggregator._traces[span.trace_id]
                # Find and remove finished children in one pass
                finished_children = []
                remaining_spans = []
                for s in trace.spans:
                    if s.finished and s.span_id != span.span_id:
                        finished_children.append(s)
                    else:
                        remaining_spans.append(s)
                trace.spans[:] = remaining_spans
                # Update the finished count
                trace.num_finished -= len(finished_children)
        tracer._span_aggregator.writer.write([partial_span] + finished_children)

    def _create_resubmit_timer(self, submission_id):
        timer = threading.Timer(
            config.ray.resubmit_interval, self._resubmit_long_running_spans, args=[submission_id]
        )
        timer.daemon = True
        self._timers[submission_id] = timer
        timer.start()

    def _recreate_job_span(self, job_span):
        new_span = Span(
            name=job_span.name,
            service=job_span.service,
            span_type=job_span.span_type,
            trace_id=job_span.trace_id,
            span_id=job_span.span_id,
            parent_id=job_span.parent_id,
            context=job_span.context,
        )
        new_span.set_tag_str("component", "ray")
        new_span.start_ns = job_span.start_ns
        new_span._meta = job_span._meta.copy()
        new_span._metrics = job_span._metrics.copy()

        return new_span

    def _resubmit_long_running_spans(self, submission_id):
        with self._lock:
            if submission_id not in self._job_spans:
                return

            job_spans = self._job_spans[submission_id]
            for span in job_spans.values():
                self._emit_partial_span(span)

            self._create_resubmit_timer(submission_id)

    def _start_long_running_span(self, span):
        submission_id = self._get_submission_id(span)
        if not submission_id:
            return

        # Every spans are using the same timer
        if submission_id not in self._timers:
            self._create_resubmit_timer(submission_id)

    def _finish_span(self, span, job_info=None):
        # was_long_running
        if span.get_metric("_dd.partial_version") is not None:
            span.set_metric("_dd.partial_version", -1)
            span.set_metric("_dd.was_long_running", 1)
            span.set_tag_str("ray.job.status", "FINISHED")

        if job_info:
            span.set_metric("_dd.was_long_running", 1)
            span.set_tag_str("ray.job.status", job_info.status)
            span.set_tag_str("ray.job.message", job_info.message)

            if str(job_info.status) == "FAILED":
                span.error = 1
                span.set_tag_str("error.message", job_info.message)
        span.finish()

    def add_span(self, span):
        submission_id = self._get_submission_id(span)
        if not submission_id:
            return

        with self._lock:
            if submission_id not in self._job_spans:
                self._job_spans[submission_id] = {}
            self._job_spans[submission_id][(span.trace_id, span.span_id)] = span

    def watch_potential_long_running(self, span):
        submission_id = self._get_submission_id(span)
        if not submission_id:
            return

        with self._lock:
            if submission_id in self._job_spans:
                if (span.trace_id, span.span_id) in self._job_spans[submission_id]:
                    # Mark the original span as long-running, but keep it open to preserve context
                    span.set_metric("_dd.partial_version", time.time_ns())
                    span.set_tag_str("ray.job.status", "RUNNING")
                    self._emit_partial_span(span)
                    self._start_long_running_span(span)

    def stop_long_running_span(self, span_to_stop):
        submission_id = self._get_submission_id(span_to_stop)
        if not submission_id:
            return

        with self._lock:
            self._finish_span(span_to_stop)

            job_spans = self._job_spans.get(submission_id, {})
            span_key = (span_to_stop.trace_id, span_to_stop.span_id)
            if span_key in job_spans:
                del job_spans[span_key]

    def stop_long_running_job(self, submission_id, job_info):
        with self._lock:
            if submission_id not in self._root_spans:
                return

            job_span = self._root_spans[submission_id]

            if submission_id in self._timers:
                self._timers.pop(submission_id).cancel()

            self._finish_span(job_span, job_info=job_info)

            del self._job_spans[submission_id]
            del self._root_spans[submission_id]


_job_manager = LongRunningJobManager()


def start_long_running_job(job_span):
    submission_id = _job_manager._get_submission_id(job_span)
    if not submission_id:
        return

    _job_manager._root_spans[submission_id] = job_span
    start_long_running_span(job_span)


def stop_long_running_job(submission_id, job_info):
    _job_manager.stop_long_running_job(submission_id, job_info)


def start_long_running_span(span):
    _job_manager.add_span(span)

    watch_timer = threading.Timer(config.ray.watch_delay, _job_manager.watch_potential_long_running, args=[span])
    watch_timer.daemon = True
    watch_timer.start()


def stop_long_running_span(span):
    _job_manager.stop_long_running_span(span)