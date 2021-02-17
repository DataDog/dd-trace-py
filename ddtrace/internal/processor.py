import threading
from typing import List
from typing import Optional

from ddtrace import Span
from ddtrace.filters import TraceFilter
from ddtrace.vendor import attr

from .logger import get_logger


log = get_logger(__name__)


@attr.s
class Stitcher(object):
    _partial_flush_enabled = attr.ib(type=bool)  # type: bool
    _partial_flush_min_spans = attr.ib(type=int)  # type: int
    _traces = attr.ib(factory=dict, init=False)  # type: List[_Trace]

    @attr.s
    class _Trace(object):
        spans = attr.ib(default=attr.Factory(list))  # type: List[Span]
        num_finished = attr.ib(type=int, default=0)  # type: int

    def _get_or_create_trace(self, trace_id):
        # type: (int) -> Stitcher._Trace
        if trace_id in self._traces:
            return self._traces[trace_id]
        else:
            trace = self._Trace()
            self._traces[trace_id] = trace
            return trace

    def add_span(self, span):
        # type: (Span) -> None
        trace = self._get_or_create_trace(span.trace_id)
        trace.spans.append(span)

    def finish_span(self, span):
        # type: (Span) -> Optional[List[Span]]
        trace = self._get_or_create_trace(span.trace_id)
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
        return None


@attr.s(eq=False)
class SpanProcessor(object):
    _filters = attr.ib(type=List[TraceFilter])
    _partial_flush_enabled = attr.ib(type=bool)
    _partial_flush_min_spans = attr.ib(type=int)
    _stitcher = attr.ib(init=False, type=Stitcher)
    _lock = attr.ib(init=False, factory=threading.Lock)

    def __attrs_post_init__(self):
        self._stitcher = Stitcher(self._partial_flush_enabled, self._partial_flush_min_spans)

    def on_span_start(self, span):
        # type: (Span) -> None
        with self._lock:
            self._stitcher.add_span(span)

    def on_span_finish(self, span):
        # type: (Span) -> Optional[List[Span]]
        with self._lock:
            trace = self._stitcher.finish_span(span)

        if trace is None:
            return

        for filtr in self._filters:
            try:
                trace = filtr.process_trace(trace)
            except Exception:
                log.error("error applying filter %r to traces", filtr, exc_info=True)
            else:
                if trace is None:
                    log.debug("dropping trace due to filter %r", filter)
                    return

        sampled = any(s.sampled for s in trace)
        if not sampled:
            log.info("dropping trace (%d spans) due to sampling decision", len(trace))
            return

        return trace
