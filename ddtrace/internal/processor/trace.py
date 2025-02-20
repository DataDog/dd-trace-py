import abc
from collections import defaultdict
from threading import Lock
from threading import RLock
from typing import Dict
from typing import Iterable
from typing import List
from typing import Optional
from typing import Union

import attr
import six

from ddtrace import config
from ddtrace.constants import BASE_SERVICE_KEY
from ddtrace.constants import SAMPLING_PRIORITY_KEY
from ddtrace.constants import SPAN_KIND
from ddtrace.constants import USER_KEEP
from ddtrace.internal import gitmetadata
from ddtrace.internal import telemetry
from ddtrace.internal.constants import HIGHER_ORDER_TRACE_ID_BITS
from ddtrace.internal.constants import MAX_UINT_64BITS
from ddtrace.internal.logger import get_logger
from ddtrace.internal.processor import SpanProcessor
from ddtrace.internal.sampling import SpanSamplingRule
from ddtrace.internal.sampling import is_single_span_sampled
from ddtrace.internal.schema import schematize_service_name
from ddtrace.internal.service import ServiceStatusError
from ddtrace.internal.telemetry.constants import TELEMETRY_NAMESPACE_TAG_TRACER
from ddtrace.internal.writer import TraceWriter
from ddtrace.span import Span
from ddtrace.span import _get_64_highest_order_bits_as_hex
from ddtrace.span import _is_top_level


try:
    from typing import DefaultDict
except ImportError:
    from collections import defaultdict as DefaultDict

log = get_logger(__name__)


@attr.s
class TraceProcessor(six.with_metaclass(abc.ABCMeta)):
    def __attrs_post_init__(self):
        # type: () -> None
        """Default post initializer which logs the representation of the
        TraceProcessor at the ``logging.DEBUG`` level.

        The representation can be modified with the ``repr`` argument to the
        attrs attribute::

            @attr.s
            class MyTraceProcessor(TraceProcessor):
                field_to_include = attr.ib(repr=True)
                field_to_exclude = attr.ib(repr=False)
        """
        log.debug("initialized trace processor %r", self)

    @abc.abstractmethod
    def process_trace(self, trace):
        # type: (List[Span]) -> Optional[List[Span]]
        """Processes a trace.

        ``None`` can be returned to prevent the trace from being further
        processed.
        """
        pass


@attr.s
class TraceSamplingProcessor(TraceProcessor):
    """Processor that keeps traces that have sampled spans. If all spans
    are unsampled then ``None`` is returned.

    Note that this processor is only effective if complete traces are sent. If
    the spans of a trace are divided in separate lists then it's possible that
    parts of the trace are unsampled when the whole trace should be sampled.
    """

    _compute_stats_enabled = attr.ib(type=bool)

    def process_trace(self, trace):
        # type: (List[Span]) -> Optional[List[Span]]
        if trace:
            # When stats computation is enabled in the tracer then we can
            # safely drop the traces.
            if self._compute_stats_enabled:
                priority = trace[0]._context.sampling_priority if trace[0]._context is not None else None
                if priority is not None and priority <= 0:
                    # When any span is marked as keep by a single span sampling
                    # decision then we still send all and only those spans.
                    single_spans = [_ for _ in trace if is_single_span_sampled(_)]

                    return single_spans or None

            for span in trace:
                if span.sampled:
                    return trace

            log.debug("dropping trace %d with %d spans", trace[0].trace_id, len(trace))

        return None


@attr.s
class TopLevelSpanProcessor(SpanProcessor):
    """Processor marks spans as top level

    A span is top level when it is the entrypoint method for a request to a service.
    Top level span and service entry span are equivalent terms

    The "top level" metric will be used by the agent to calculate trace metrics
    and determine how spans should be displaced in the UI. If this metric is not
    set by the tracer the first span in a trace chunk will be marked as top level.

    """

    def on_span_start(self, _):
        # type: (Span) -> None
        pass

    def on_span_finish(self, span):
        # DEV: Update span after finished to avoid race condition
        if _is_top_level(span):
            span.set_metric("_dd.top_level", 1)


@attr.s
class TraceTagsProcessor(TraceProcessor):
    """Processor that applies trace-level tags to the trace."""

    def _set_git_metadata(self, chunk_root):
        repository_url, commit_sha = gitmetadata.get_git_tags()
        if repository_url:
            chunk_root.set_tag_str("_dd.git.repository_url", repository_url)
        if commit_sha:
            chunk_root.set_tag_str("_dd.git.commit.sha", commit_sha)

    def process_trace(self, trace):
        # type: (List[Span]) -> Optional[List[Span]]
        if not trace:
            return trace

        chunk_root = trace[0]
        ctx = chunk_root._context
        if not ctx:
            return trace

        ctx._update_tags(chunk_root)
        self._set_git_metadata(chunk_root)
        chunk_root.set_tag_str("language", "python")
        # for 128 bit trace ids
        if chunk_root.trace_id > MAX_UINT_64BITS:
            trace_id_hob = _get_64_highest_order_bits_as_hex(chunk_root.trace_id)
            chunk_root.set_tag_str(HIGHER_ORDER_TRACE_ID_BITS, trace_id_hob)
        return trace


@attr.s
class SpanAggregator(SpanProcessor):
    """Processor that aggregates spans together by trace_id and writes the
    spans to the provided writer when:
        - The collection is assumed to be complete. A collection of spans is
          assumed to be complete if all the spans that have been created with
          the trace_id have finished; or
        - A minimum threshold of spans (``partial_flush_min_spans``) have been
          finished in the collection and ``partial_flush_enabled`` is True.
    """

    @attr.s
    class _Trace(object):
        spans = attr.ib(default=attr.Factory(list))  # type: List[Span]
        num_finished = attr.ib(type=int, default=0)  # type: int

    _partial_flush_enabled = attr.ib(type=bool)
    _partial_flush_min_spans = attr.ib(type=int)
    _trace_processors = attr.ib(type=Iterable[TraceProcessor])
    _writer = attr.ib(type=TraceWriter)
    _traces = attr.ib(
        factory=lambda: defaultdict(lambda: SpanAggregator._Trace()),
        init=False,
        type=DefaultDict[int, "_Trace"],
        repr=False,
    )
    if config._span_aggregator_rlock:
        _lock = attr.ib(init=False, factory=RLock, repr=False, type=Union[RLock, Lock])
    else:
        _lock = attr.ib(init=False, factory=Lock, repr=False, type=Union[RLock, Lock])
    # Tracks the number of spans created and tags each count with the api that was used
    # ex: otel api, opentracing api, datadog api
    _span_metrics = attr.ib(
        init=False,
        factory=lambda: {
            "spans_created": defaultdict(int),
            "spans_finished": defaultdict(int),
        },
        type=Dict[str, DefaultDict],
    )

    def on_span_start(self, span):
        # type: (Span) -> None
        with self._lock:
            trace = self._traces[span.trace_id]
            trace.spans.append(span)
            self._span_metrics["spans_created"][span._span_api] += 1
            self._queue_span_count_metrics("spans_created", "integration_name")

    def on_span_finish(self, span):
        # type: (Span) -> None
        with self._lock:
            self._span_metrics["spans_finished"][span._span_api] += 1
            trace = self._traces[span.trace_id]
            trace.num_finished += 1
            should_partial_flush = self._partial_flush_enabled and trace.num_finished >= self._partial_flush_min_spans
            if trace.num_finished == len(trace.spans) or should_partial_flush:
                trace_spans = trace.spans
                trace.spans = []
                if trace.num_finished < len(trace_spans):
                    finished = []
                    for s in trace_spans:
                        if s.finished:
                            finished.append(s)
                        else:
                            trace.spans.append(s)
                else:
                    finished = trace_spans

                num_finished = len(finished)

                if should_partial_flush and num_finished > 0:
                    log.debug("Partially flushing %d spans for trace %d", num_finished, span.trace_id)
                    finished[0].set_metric("_dd.py.partial_flush", num_finished)

                trace.num_finished -= num_finished

                if len(trace.spans) == 0:
                    del self._traces[span.trace_id]

                spans = finished  # type: Optional[List[Span]]
                for tp in self._trace_processors:
                    try:
                        if spans is None:
                            return
                        spans = tp.process_trace(spans)
                    except Exception:
                        log.error("error applying processor %r", tp, exc_info=True)

                self._queue_span_count_metrics("spans_finished", "integration_name")
                self._writer.write(spans)
                return

            log.debug("trace %d has %d spans, %d finished", span.trace_id, len(trace.spans), trace.num_finished)
            return None

    def shutdown(self, timeout):
        # type: (Optional[float]) -> None
        """
        This will stop the background writer/worker and flush any finished traces in the buffer. The tracer cannot be
        used for tracing after this method has been called. A new tracer instance is required to continue tracing.

        :param timeout: How long in seconds to wait for the background worker to flush traces
            before exiting or :obj:`None` to block until flushing has successfully completed (default: :obj:`None`)
        :type timeout: :obj:`int` | :obj:`float` | :obj:`None`
        """
        if self._span_metrics["spans_created"] or self._span_metrics["spans_finished"]:
            if config._telemetry_enabled:
                # Telemetry writer is disabled when a process shutsdown. This is to support py3.12.
                # Here we submit the remanining span creation metrics without restarting the periodic thread.
                # Note - Due to how atexit hooks are registered the telemetry writer is shutdown before the tracer.
                telemetry.telemetry_writer._is_periodic = False
                telemetry.telemetry_writer._enabled = True
                # on_span_start queue span created counts in batches of 100. This ensures all remaining counts are sent
                # before the tracer is shutdown.
                self._queue_span_count_metrics("spans_created", "integration_name", None)
                # on_span_finish(...) queues span finish metrics in batches of 100.
                # This ensures all remaining counts are sent before the tracer is shutdown.
                self._queue_span_count_metrics("spans_finished", "integration_name", None)
                telemetry.telemetry_writer.periodic(True)
                # Disable the telemetry writer so no events/metrics/logs are queued during process shutdown
                telemetry.telemetry_writer.disable()

        try:
            self._writer.stop(timeout)
        except ServiceStatusError:
            # It's possible the writer never got started in the first place :(
            pass

    def _queue_span_count_metrics(self, metric_name, tag_name, min_count=100):
        # type: (str, str, Optional[int]) -> None
        """Queues a telemetry count metric for span created and span finished"""
        # perf: telemetry_metrics_writer.add_count_metric(...) is an expensive operation.
        # We should avoid calling this method on every invocation of span finish and span start.
        if min_count is None or sum(self._span_metrics[metric_name].values()) >= min_count:
            for tag_value, count in self._span_metrics[metric_name].items():
                telemetry.telemetry_writer.add_count_metric(
                    TELEMETRY_NAMESPACE_TAG_TRACER, metric_name, count, tags=((tag_name, tag_value),)
                )
            self._span_metrics[metric_name] = defaultdict(int)


@attr.s
class SpanSamplingProcessor(SpanProcessor):
    """SpanProcessor for sampling single spans:

    * Span sampling must be applied after trace sampling priority has been set.
    * Span sampling rules are specified with a sample rate or rate limit as well as glob patterns
      for matching spans on service and name.
    * If the span sampling decision is to keep the span, then span sampling metrics are added to the span.
    * If a dropped trace includes a span that had been kept by a span sampling rule, then the span is sent to the
      Agent even if the dropped trace is not (as is the case when trace stats computation is enabled).
    """

    rules = attr.ib(type=List[SpanSamplingRule])

    def on_span_start(self, span):
        # type: (Span) -> None
        pass

    def on_span_finish(self, span):
        # type: (Span) -> None
        # only sample if the span isn't already going to be sampled by trace sampler
        if span.context.sampling_priority is not None and span.context.sampling_priority <= 0:
            for rule in self.rules:
                if rule.match(span):
                    rule.sample(span)
                    # If stats computation is enabled, we won't send all spans to the agent.
                    # In order to ensure that the agent does not update priority sampling rates
                    # due to single spans sampling, we set all of these spans to manual keep.
                    if config._trace_compute_stats:
                        span.set_metric(SAMPLING_PRIORITY_KEY, USER_KEEP)
                    break


class PeerServiceProcessor(TraceProcessor):
    def __init__(self, peer_service_config):
        self._config = peer_service_config
        self._set_defaults_enabled = self._config.set_defaults_enabled
        self._mapping = self._config.peer_service_mapping

    def process_trace(self, trace):
        if not trace:
            return

        traces_to_process = []
        if not self._set_defaults_enabled:
            traces_to_process = filter(lambda x: x.get_tag(self._config.tag_name), trace)
        else:
            traces_to_process = filter(
                lambda x: x.get_tag(self._config.tag_name) or x.get_tag(SPAN_KIND) in self._config.enabled_span_kinds,
                trace,
            )
        any(map(lambda x: self._update_peer_service_tags(x), traces_to_process))

        return trace

    def _update_peer_service_tags(self, span):
        tag = span.get_tag(self._config.tag_name)

        if tag:  # If the tag already exists, assume it is user generated
            span.set_tag_str(self._config.source_tag_name, self._config.tag_name)
        else:
            for data_source in self._config.prioritized_data_sources:
                tag = span.get_tag(data_source)
                if tag:
                    span.set_tag_str(self._config.tag_name, tag)
                    span.set_tag_str(self._config.source_tag_name, data_source)
                    break

        if tag in self._mapping:
            span.set_tag_str(self._config.remap_tag_name, tag)
            span.set_tag_str(self._config.tag_name, self._config.peer_service_mapping[tag])


class BaseServiceProcessor(TraceProcessor):
    def __init__(self):
        self._global_service = schematize_service_name((config.service or "").lower())

    def process_trace(self, trace):
        if not trace:
            return

        traces_to_process = filter(
            lambda x: x.service and x.service.lower() != self._global_service,
            trace,
        )
        any(map(lambda x: self._update_dd_base_service(x), traces_to_process))

        return trace

    def _update_dd_base_service(self, span):
        span.set_tag_str(key=BASE_SERVICE_KEY, value=self._global_service)
