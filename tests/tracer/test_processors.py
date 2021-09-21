from typing import Any

import attr
import mock
import pytest

from ddtrace import Span
from ddtrace.internal.processor import SpanProcessor
from ddtrace.internal.processor.trace import SpanAggregator
from ddtrace.internal.processor.trace import TraceProcessor
from tests.utils import DummyWriter


def test_no_impl():
    @attr.s
    class BadProcessor(SpanProcessor):
        pass

    with pytest.raises(TypeError):
        BadProcessor()


def test_default_post_init():
    @attr.s
    class MyProcessor(SpanProcessor):
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


def test_aggregator_single_span():
    class Proc(TraceProcessor):
        def process_trace(self, trace):
            return trace

    mock_proc1 = mock.Mock(wraps=Proc())
    mock_proc2 = mock.Mock(wraps=Proc())
    writer = DummyWriter()
    aggr = SpanAggregator(
        partial_flush_enabled=False,
        partial_flush_min_spans=0,
        trace_processors=[
            mock_proc1,
            mock_proc2,
        ],
        writer=writer,
    )

    span = Span(None, "span", on_finish=[aggr.on_span_finish])
    aggr.on_span_start(span)
    span.finish()

    mock_proc1.process_trace.assert_called_with([span])
    mock_proc2.process_trace.assert_called_with([span])
    assert writer.pop() == [span]


def test_aggregator_bad_processor():
    class Proc(TraceProcessor):
        def process_trace(self, trace):
            return trace

    class BadProc(TraceProcessor):
        def process_trace(self, trace):
            raise ValueError

    mock_good_before = mock.Mock(wraps=Proc())
    mock_bad = mock.Mock(wraps=BadProc())
    mock_good_after = mock.Mock(wraps=Proc())
    writer = DummyWriter()
    aggr = SpanAggregator(
        partial_flush_enabled=False,
        partial_flush_min_spans=0,
        trace_processors=[
            mock_good_before,
            mock_bad,
            mock_good_after,
        ],
        writer=writer,
    )

    span = Span(None, "span", on_finish=[aggr.on_span_finish])
    aggr.on_span_start(span)
    span.finish()

    mock_good_before.process_trace.assert_called_with([span])
    mock_bad.process_trace.assert_called_with([span])
    mock_good_after.process_trace.assert_called_with([span])
    assert writer.pop() == [span]


def test_aggregator_multi_span():
    writer = DummyWriter()
    aggr = SpanAggregator(partial_flush_enabled=False, partial_flush_min_spans=0, trace_processors=[], writer=writer)

    # Normal usage
    parent = Span(None, "parent", on_finish=[aggr.on_span_finish])
    aggr.on_span_start(parent)
    child = Span(None, "child", on_finish=[aggr.on_span_finish])
    child.trace_id = parent.trace_id
    child.parent_id = parent.span_id
    aggr.on_span_start(child)

    assert writer.pop() == []
    child.finish()
    assert writer.pop() == []
    parent.finish()
    assert writer.pop() == [parent, child]

    # Parent closes before child
    parent = Span(None, "parent", on_finish=[aggr.on_span_finish])
    aggr.on_span_start(parent)
    child = Span(None, "child", on_finish=[aggr.on_span_finish])
    child.trace_id = parent.trace_id
    child.parent_id = parent.span_id
    aggr.on_span_start(child)

    assert writer.pop() == []
    parent.finish()
    assert writer.pop() == []
    child.finish()
    assert writer.pop() == [parent, child]


def test_aggregator_partial_flush_0_spans():
    writer = DummyWriter()
    aggr = SpanAggregator(partial_flush_enabled=True, partial_flush_min_spans=0, trace_processors=[], writer=writer)

    # Normal usage
    parent = Span(None, "parent", on_finish=[aggr.on_span_finish])
    aggr.on_span_start(parent)
    child = Span(None, "child", on_finish=[aggr.on_span_finish])
    child.trace_id = parent.trace_id
    child.parent_id = parent.span_id
    aggr.on_span_start(child)

    assert writer.pop() == []
    child.finish()
    assert writer.pop() == [child]
    parent.finish()
    assert writer.pop() == [parent]

    # Parent closes before child
    parent = Span(None, "parent", on_finish=[aggr.on_span_finish])
    aggr.on_span_start(parent)
    child = Span(None, "child", on_finish=[aggr.on_span_finish])
    child.trace_id = parent.trace_id
    child.parent_id = parent.span_id
    aggr.on_span_start(child)

    assert writer.pop() == []
    parent.finish()
    assert writer.pop() == [parent]
    child.finish()
    assert writer.pop() == [child]


def test_aggregator_partial_flush_2_spans():
    writer = DummyWriter()
    aggr = SpanAggregator(partial_flush_enabled=True, partial_flush_min_spans=2, trace_processors=[], writer=writer)

    # Normal usage
    parent = Span(None, "parent", on_finish=[aggr.on_span_finish])
    aggr.on_span_start(parent)
    child = Span(None, "child", on_finish=[aggr.on_span_finish])
    child.trace_id = parent.trace_id
    child.parent_id = parent.span_id
    aggr.on_span_start(child)

    assert writer.pop() == []
    child.finish()
    assert writer.pop() == []
    parent.finish()
    assert writer.pop() == [parent, child]

    # Parent closes before child
    parent = Span(None, "parent", on_finish=[aggr.on_span_finish])
    aggr.on_span_start(parent)
    child = Span(None, "child", on_finish=[aggr.on_span_finish])
    child.trace_id = parent.trace_id
    child.parent_id = parent.span_id
    aggr.on_span_start(child)

    assert writer.pop() == []
    parent.finish()
    assert writer.pop() == []
    child.finish()
    assert writer.pop() == [parent, child]

    # Partial flush
    parent = Span(None, "parent", on_finish=[aggr.on_span_finish])
    aggr.on_span_start(parent)
    child1 = Span(None, "child1", on_finish=[aggr.on_span_finish])
    child1.trace_id = parent.trace_id
    child1.parent_id = parent.span_id
    aggr.on_span_start(child1)
    child2 = Span(None, "child2", on_finish=[aggr.on_span_finish])
    child2.trace_id = parent.trace_id
    child2.parent_id = parent.span_id
    aggr.on_span_start(child2)

    assert writer.pop() == []
    child1.finish()
    assert writer.pop() == []
    child2.finish()
    assert writer.pop() == [child1, child2]
    parent.finish()
    assert writer.pop() == [parent]
