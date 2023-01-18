import abc
from collections import defaultdict
import threading
from typing import DefaultDict
from typing import Iterable
from typing import List
from typing import Optional

import attr
import six

from ddtrace import config
from ddtrace.constants import SAMPLING_PRIORITY_KEY
from ddtrace.constants import USER_KEEP
from ddtrace.internal.logger import get_logger
from ddtrace.internal.processor import SpanProcessor
from ddtrace.internal.sampling import SpanSamplingRule
from ddtrace.internal.sampling import is_single_span_sampled
from ddtrace.internal.service import ServiceStatusError
from ddtrace.internal.writer import TraceWriter
from ddtrace.span import Span
from ddtrace.span import _is_top_level


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

    def process_trace(self, trace):
        # type: (List[Span]) -> Optional[List[Span]]
        if not trace:
            return trace

        chunk_root = trace[0]
        ctx = chunk_root._context
        if not ctx:
            return trace

        ctx._update_tags(chunk_root)
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
    _lock = attr.ib(init=False, factory=threading.Lock, repr=False)

    def on_span_start(self, span):
        # type: (Span) -> None
        with self._lock:
            trace = self._traces[span.trace_id]
            trace.spans.append(span)

    def on_span_finish(self, span):
        # type: (Span) -> None
        with self._lock:
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

                if should_partial_flush:
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
        try:
            self._writer.stop(timeout)
        except ServiceStatusError:
            # It's possible the writer never got started in the first place :(
            pass


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
