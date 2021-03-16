import mock

from ddtrace import Span
from ddtrace.filters import TraceFilter
from ddtrace.internal.processor import TraceProcessor


def test_no_filters():
    tp = TraceProcessor([])
    trace = [Span(None, "span1"), Span(None, "span2")]
    spans = tp.process(trace)
    assert spans == trace


def test_single_filter():
    class Filter(TraceFilter):
        def process_trace(self, trace):
            return None

    tp = TraceProcessor([Filter()])
    trace = [Span(None, "span1"), Span(None, "span2")]
    spans = tp.process(trace)
    assert spans is None


def test_multi_filter_none():
    class Filter(TraceFilter):
        def process_trace(self, trace):
            return None

    tp = TraceProcessor([Filter(), Filter()])
    trace = [Span(None, "span1"), Span(None, "span2")]
    spans = tp.process(trace)
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

    tp = TraceProcessor([Filter(), Filter2()])
    trace = [Span(None, "span1"), Span(None, "span2")]
    spans = tp.process(trace)

    assert [s.get_tag("test") for s in spans] == ["value", "value2"]


def test_filter_error():
    class Filter(TraceFilter):
        def process_trace(self, trace):
            raise Exception()

    f = Filter()
    with mock.patch("ddtrace.internal.processor.log") as log:
        tp = TraceProcessor([f])
        trace = [Span(None, "span1"), Span(None, "span2")]
        spans = tp.process(trace)

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
        tp = TraceProcessor([f1, f2])
        trace = [Span(None, "span1"), Span(None, "span2")]
        spans = tp.process(trace)

    assert spans == trace
    calls = [
        mock.call("error applying filter %r to traces", f1, exc_info=True),
        mock.call("error applying filter %r to traces", f2, exc_info=True),
    ]
    log.error.assert_has_calls(calls)
