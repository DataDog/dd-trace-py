from typing import Any

import attr
import mock
import pytest

from ddtrace import Span
from ddtrace import Tracer
from ddtrace.internal.processor import SpanProcessor
from ddtrace.internal.processor.trace import SpanAggregator
from ddtrace.internal.processor.trace import TraceProcessor
from ddtrace.internal.processor.trace import TraceTopLevelSpanProcessor
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


def test_trace_top_level_span_processor_partial_flushing():
    """Parent span and child span have the same service name"""
    tracer = Tracer()
    tracer.configure(
        partial_flush_enabled=True,
        partial_flush_min_spans=2,
        writer=DummyWriter(),
    )

    with tracer.trace("parent") as parent:
        with tracer.trace("1") as child1:
            pass
        with tracer.trace("2") as child2:
            pass
        with tracer.trace("3") as child3:
            pass

    # child spans 1 and 2 were partial flushed WITHOUT the parent span in the trace chunk
    assert child1.get_metric("_dd.top_level") == 0
    assert child2.get_metric("_dd.top_level") == 0

    # child span 3 was partial flushed WITH the parent span in the trace chunk
    assert "_dd.top_level" not in child3.metrics
    assert parent.get_metric("_dd.top_level") == 1


def test_trace_top_level_span_processor_same_service_name():
    """Parent span and child span have the same service name"""

    tracer = Tracer()
    tracer.configure(writer=DummyWriter())

    with tracer.trace("parent", service="top_level_test") as parent:
        with tracer.trace("child") as child:
            pass

    assert parent.get_metric("_dd.top_level") == 1
    assert "_dd.top_level" not in child.metrics


def test_trace_top_level_span_processor_different_service_name():
    """Parent span and child span have the different service names"""

    tracer = Tracer()
    tracer.configure(writer=DummyWriter())

    with tracer.trace("parent", service="top_level_test_service") as parent:
        with tracer.trace("child", service="top_level_test_service2") as child:
            pass

    assert parent.get_metric("_dd.top_level") == 1
    assert child.get_metric("_dd.top_level") == 1


def test_trace_top_level_span_processor_orphan_span():
    """Trace chuck does not contain parent span"""

    tracer = Tracer()
    tracer.configure(writer=DummyWriter())

    with tracer.trace("parent") as parent:
        pass

    with tracer.start_span("orphan span", child_of=parent) as orphan_span:
        pass

    # top_level in orphan_span should be explicitly set to zero/false
    assert orphan_span.get_metric("_dd.top_level") == 0


def test_trace_top_level_span_processor_trace_return_val():
    """TraceProcessor returns spans"""
    trace_processors = TraceTopLevelSpanProcessor()
    # Trace contains no spans
    trace = []
    assert trace_processors.process_trace(trace) == trace

    trace = [Span(None, "span1"), Span(None, "span2"), Span(None, "span3")]
    # Test return value contains all spans in the argument
    assert trace_processors.process_trace(trace[:]) == trace
