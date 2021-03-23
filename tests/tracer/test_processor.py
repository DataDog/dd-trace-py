from typing import Any

import mock
import pytest

from ddtrace import Span
from ddtrace.filters import TraceFilter
from ddtrace.internal.processor import Processor
from ddtrace.internal.processor import TraceFiltersProcessor
from ddtrace.vendor import attr


def test_no_impl():
    @attr.s
    class BadProcessor(Processor):
        pass

    with pytest.raises(TypeError):
        BadProcessor()


def test_default_post_init():
    @attr.s
    class MyProcessor(Processor):
        def on_span_start(self, span):  # type: (Span) -> None
            pass

        def on_span_finish(self, data):  # type: (Any) -> Any
            pass

    with mock.patch("ddtrace.internal.processor.log") as log:
        p = MyProcessor()

    calls = [
        mock.call("initialized processor %r", p),
    ]
    log.debug.assert_has_calls(calls)


def test_no_filters():
    tp = TraceFiltersProcessor([])
    trace = [Span(None, "span1"), Span(None, "span2")]
    spans = tp.on_span_finish(trace)
    assert spans == trace


def test_single_filter():
    class Filter(TraceFilter):
        def process_trace(self, trace):
            return None

    tp = TraceFiltersProcessor([Filter()])
    trace = [Span(None, "span1"), Span(None, "span2")]
    spans = tp.on_span_finish(trace)
    assert spans is None


def test_multi_filter_none():
    class Filter(TraceFilter):
        def process_trace(self, trace):
            return None

    f1 = Filter()
    f2 = Filter()
    with mock.patch("ddtrace.internal.processor.log") as log:
        tp = TraceFiltersProcessor([f1, f2])
        trace = [Span(None, "span1"), Span(None, "span2")]
        spans = tp.on_span_finish(trace)

    calls = [
        mock.call("initialized processor %r", tp),
        mock.call("applying filter %r to %s", f1, trace[0].trace_id),
        mock.call("dropping trace due to filter %r", f1),
    ]
    log.debug.assert_has_calls(calls)
    assert spans is None


def test_multi_filter_mutate():
    class Filter(TraceFilter):
        def process_trace(self, trace):
            trace[0].set_tag("test", "value")
            return trace

    class Filter2(TraceFilter):
        def process_trace(self, trace):
            trace[1].set_tag("test", "value2")
            return trace

    tp = TraceFiltersProcessor([Filter(), Filter2()])
    trace = [Span(None, "span1"), Span(None, "span2")]
    spans = tp.on_span_finish(trace)

    assert [s.get_tag("test") for s in spans] == ["value", "value2"]


def test_filter_error():
    class Filter(TraceFilter):
        def process_trace(self, trace):
            raise Exception()

    f = Filter()
    with mock.patch("ddtrace.internal.processor.log") as log:
        tp = TraceFiltersProcessor([f])
        trace = [Span(None, "span1"), Span(None, "span2")]
        spans = tp.on_span_finish(trace)

    calls = [mock.call("error applying filter %r to traces", f, exc_info=True)]
    log.error.assert_has_calls(calls)
    assert spans == trace


def test_filter_error_multi():
    class Filter(TraceFilter):
        def process_trace(self, trace):
            raise Exception()

    class Filter2(TraceFilter):
        def process_trace(self, trace):
            raise Exception()

    f1 = Filter()
    f2 = Filter2()

    with mock.patch("ddtrace.internal.processor.log") as log:
        tp = TraceFiltersProcessor([f1, f2])
        trace = [Span(None, "span1"), Span(None, "span2")]
        spans = tp.on_span_finish(trace)

    assert spans == trace
    calls = [
        mock.call("error applying filter %r to traces", f1, exc_info=True),
        mock.call("error applying filter %r to traces", f2, exc_info=True),
    ]
    log.error.assert_has_calls(calls)


def test_spans_to_trace_processor():
    pass
