import abc
from collections import defaultdict
import threading
from typing import Any
from typing import DefaultDict
from typing import List
from typing import Optional

from ddtrace import Span
from ddtrace.filters import TraceFilter
from ddtrace.vendor import attr
from ddtrace.vendor import six

from .logger import get_logger


log = get_logger(__name__)


@attr.s
class Processor(six.with_metaclass(abc.ABCMeta)):
    """A Processor is used to process spans as they are created and finished by a tracer."""

    def __attrs_post_init__(self):
        # type () -> None
        """Default post initializer which logs the representation of the
        Processor at the ``logging.DEBUG`` level.

        The representation can be modified with the ``repr`` argument to the
        attrs attribute::

            @attr.s
            class MyProcessor(Processor):
                field_to_include = attr.ib(repr=True)
                field_to_exclude = attr.ib(repr=False)
        """
        log.debug("initialized processor %r", self)

    @abc.abstractmethod
    def on_span_start(self, span):
        # type: (Span) -> None
        """Called when a span is started.

        This method is useful for making upfront decisions on spans.

        For example, a sampling decision can be made when the span is created
        based on its resource name.
        """
        pass

    @abc.abstractmethod
    def on_span_finish(self, data):
        # type: (Any) -> Any
        """Called with the result of any previous processors or initially with
        the finishing span when a span finishes.

        It can return any data which will be passed to any processors that are
        applied afterwards.
        """
        pass


@attr.s(eq=False)
class TraceFiltersProcessor(Processor):
    """Processor for applying a list of ``TraceFilter`` to traces.

    The filters are applied sequentially with the result of one passed on to
    the next. If ``None`` is returned by a filter then execution short-circuits
    and ``None`` is returned.

    If a filter raises an exception it will be logged at the ``logging.ERROR``
    level and the filter chain will continue with the next filter in the list.
    """

    _filters = attr.ib(type=List[TraceFilter], repr=True)

    def on_span_start(self, span):
        # type: (Span) -> None
        return None

    def on_span_finish(self, trace):
        # type: (Optional[List[Span]]) -> Optional[List[Span]]
        if trace is None:
            return

        for filtr in self._filters:
            try:
                log.debug("applying filter %r to %s", filtr, trace[0].trace_id if trace else [])
                trace = filtr.process_trace(trace)
            except Exception:
                log.error("error applying filter %r to traces", filtr, exc_info=True)
            else:
                if trace is None:
                    log.debug("dropping trace due to filter %r", filtr)
                    return

        return trace


@attr.s
class TraceSamplingProcessor(Processor):
    def on_span_start(self, span):
        # type: (Span) -> None
        # TODO: move tracer sampling decision logic here.
        pass

    def on_span_finish(self, trace):
        # type: (Optional[List[Span]]) -> Optional[List[Span]]
        if trace is None:
            return None

        sampled_spans = [s.sampled for s in trace]
        if len(sampled_spans) == 0:
            log.info("dropping trace, %d spans unsampled", len(trace))
            return

        log.info("trace %d sampled (%d/%d spans sampled)", trace[0].trace_id, len(sampled_spans), len(trace))
        return trace


@attr.s
class SpansToTraceProcessor(Processor):
    """Processor that aggregates spans together by trace_id and submits the
    finalized trace if the trace is assumed to be complete[0] or if a partial
    flushing threshold has been met[1].

    [0] A trace is assumed to be complete if all the spans that have been
    created with the trace_id have finished. When this condition is met the
    completed spans are returned.

    [1] A minimum threshold of spans can be specified with the
    ``partial_flush_min_spans`` argument. When this threshold of spans has been
    finished the finished spans of the trace are returned.
    """

    @attr.s
    class _Trace(object):
        spans = attr.ib(default=attr.Factory(list))  # type: List[Span]
        num_finished = attr.ib(type=int, default=0)  # type: int

    _partial_flush_enabled = attr.ib(type=bool)
    _partial_flush_min_spans = attr.ib(type=int)
    _traces = attr.ib(
        factory=lambda: defaultdict(lambda: SpansToTraceProcessor._Trace()),
        init=False,
        type=DefaultDict[str, "_Trace"],
        repr=False,
    )
    _lock = attr.ib(init=False, factory=threading.Lock, repr=False)

    def on_span_start(self, span):
        # type: (Span) -> None
        trace = self._traces[span.trace_id]
        trace.spans.append(span)

    def on_span_finish(self, span):
        # type: (Span) -> Optional[List[Span]]
        with self._lock:
            trace = self._traces[span.trace_id]
            trace.num_finished += 1
            if trace.num_finished == len(trace.spans) or (
                self._partial_flush_enabled and trace.num_finished >= self._partial_flush_min_spans
            ):
                finished = [s for s in trace.spans if s.finished]
                trace.spans = [s for s in trace.spans if not s.finished]
                trace.num_finished -= len(finished)

                if len(trace.spans) == 0:
                    del self._traces[span.trace_id]
                return finished
            log.debug("trace %d has %d spans, %d finished", span.trace_id, len(trace.spans), trace.num_finished)
            return None
