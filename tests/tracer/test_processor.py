from typing import Any

import mock
import pytest

from ddtrace import Span
from ddtrace.filters import TraceFilter
from ddtrace.internal.processor import Processor
from ddtrace.internal.processor import TraceFilterProcessor
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


def test_single_filter():
    class Filter(TraceFilter):
        def process_trace(self, trace):
            return None

    tp = TraceFilterProcessor(Filter())
    trace = [Span(None, "span1"), Span(None, "span2")]
    spans = tp.on_span_finish(trace)
    assert spans is None


def test_multi_filter_none():
    class Filter(TraceFilter):
        def process_trace(self, trace):
            return None

    f1 = Filter()
    with mock.patch("ddtrace.internal.processor.log") as log:
        tp = TraceFilterProcessor(f1)
        trace = [Span(None, "span1"), Span(None, "span2")]
        spans = tp.on_span_finish(trace)

    calls = [
        mock.call("initialized processor %r", tp),
        mock.call("applying filter %r to %d", f1, trace[0].trace_id),
        mock.call("trace %d dropped due to filter %r", trace[0].trace_id, f1),
    ]
    log.debug.assert_has_calls(calls)
    assert spans is None


def test_filter_error():
    class Filter(TraceFilter):
        def process_trace(self, trace):
            raise Exception()

    f = Filter()
    with mock.patch("ddtrace.internal.processor.log") as log:
        tp = TraceFilterProcessor(f)
        trace = [Span(None, "span1"), Span(None, "span2")]
        spans = tp.on_span_finish(trace)

    calls = [mock.call("error applying filter %r to traces", f, exc_info=True)]
    log.error.assert_has_calls(calls)
    assert spans == trace
