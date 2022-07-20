from typing import Any

import attr
import mock
import pytest

from ddtrace import Span
from ddtrace import Tracer
from ddtrace.constants import SAMPLING_PRIORITY_KEY
from ddtrace.constants import _SINGLE_SPAN_SAMPLING_MAX_PER_SEC
from ddtrace.constants import _SINGLE_SPAN_SAMPLING_MECHANISM
from ddtrace.constants import _SINGLE_SPAN_SAMPLING_RATE
from ddtrace.internal.processor.trace import SingleSpanSamplingProcessor
from ddtrace.internal.processor.trace import SpanAggregator
from ddtrace.internal.processor.trace import SpanProcessor
from ddtrace.internal.processor.trace import TraceProcessor
from ddtrace.internal.processor.trace import TraceTopLevelSpanProcessor
from ddtrace.internal.processor.truncator import DEFAULT_SERVICE_NAME
from ddtrace.internal.processor.truncator import DEFAULT_SPAN_NAME
from ddtrace.internal.processor.truncator import MAX_META_KEY_LENGTH
from ddtrace.internal.processor.truncator import MAX_META_VALUE_LENGTH
from ddtrace.internal.processor.truncator import MAX_METRIC_KEY_LENGTH
from ddtrace.internal.processor.truncator import MAX_RESOURCE_NAME_LENGTH
from ddtrace.internal.processor.truncator import MAX_TYPE_LENGTH
from ddtrace.internal.processor.truncator import NormalizeSpanProcessor
from ddtrace.internal.processor.truncator import TruncateSpanProcessor
from ddtrace.internal.sampling import SamplingMechanism
from ddtrace.internal.sampling import SpanSamplingRule
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

    span = Span("span", on_finish=[aggr.on_span_finish])
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

    span = Span("span", on_finish=[aggr.on_span_finish])
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
    parent = Span("parent", on_finish=[aggr.on_span_finish])
    aggr.on_span_start(parent)
    child = Span("child", on_finish=[aggr.on_span_finish])
    child.trace_id = parent.trace_id
    child.parent_id = parent.span_id
    aggr.on_span_start(child)

    assert writer.pop() == []
    child.finish()
    assert writer.pop() == []
    parent.finish()
    assert writer.pop() == [parent, child]

    # Parent closes before child
    parent = Span("parent", on_finish=[aggr.on_span_finish])
    aggr.on_span_start(parent)
    child = Span("child", on_finish=[aggr.on_span_finish])
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
    parent = Span("parent", on_finish=[aggr.on_span_finish])
    aggr.on_span_start(parent)
    child = Span("child", on_finish=[aggr.on_span_finish])
    child.trace_id = parent.trace_id
    child.parent_id = parent.span_id
    aggr.on_span_start(child)

    assert writer.pop() == []
    child.finish()
    assert writer.pop() == [child]
    parent.finish()
    assert writer.pop() == [parent]

    # Parent closes before child
    parent = Span("parent", on_finish=[aggr.on_span_finish])
    aggr.on_span_start(parent)
    child = Span("child", on_finish=[aggr.on_span_finish])
    child.trace_id = parent.trace_id
    child.parent_id = parent.span_id
    aggr.on_span_start(child)

    assert writer.pop() == []
    parent.finish()
    assert writer.pop() == [parent]
    assert parent.get_metric("_dd.py.partial_flush") == 1
    child.finish()
    assert writer.pop() == [child]
    assert child.get_metric("_dd.py.partial_flush") == 1


def test_aggregator_partial_flush_2_spans():
    writer = DummyWriter()
    aggr = SpanAggregator(partial_flush_enabled=True, partial_flush_min_spans=2, trace_processors=[], writer=writer)

    # Normal usage
    parent = Span("parent", on_finish=[aggr.on_span_finish])
    aggr.on_span_start(parent)
    child = Span("child", on_finish=[aggr.on_span_finish])
    child.trace_id = parent.trace_id
    child.parent_id = parent.span_id
    aggr.on_span_start(child)

    assert writer.pop() == []
    child.finish()
    assert writer.pop() == []
    parent.finish()
    assert writer.pop() == [parent, child]

    # Parent closes before child
    parent = Span("parent", on_finish=[aggr.on_span_finish])
    aggr.on_span_start(parent)
    child = Span("child", on_finish=[aggr.on_span_finish])
    child.trace_id = parent.trace_id
    child.parent_id = parent.span_id
    aggr.on_span_start(child)

    assert writer.pop() == []
    parent.finish()
    assert writer.pop() == []
    child.finish()
    assert writer.pop() == [parent, child]

    # Partial flush
    parent = Span("parent", on_finish=[aggr.on_span_finish])
    aggr.on_span_start(parent)
    child1 = Span("child1", on_finish=[aggr.on_span_finish])
    child1.trace_id = parent.trace_id
    child1.parent_id = parent.span_id
    aggr.on_span_start(child1)
    child2 = Span("child2", on_finish=[aggr.on_span_finish])
    child2.trace_id = parent.trace_id
    child2.parent_id = parent.span_id
    aggr.on_span_start(child2)

    assert writer.pop() == []
    child1.finish()
    assert writer.pop() == []
    child2.finish()
    assert writer.pop() == [child1, child2]
    assert child1.get_metric("_dd.py.partial_flush") == 2
    assert child2.get_metric("_dd.py.partial_flush") is None
    parent.finish()
    assert writer.pop() == [parent]
    assert parent.get_metric("_dd.py.partial_flush") is None


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
    assert "_dd.top_level" not in child3.get_metrics()
    assert parent.get_metric("_dd.top_level") == 1


def test_trace_top_level_span_processor_same_service_name():
    """Parent span and child span have the same service name"""

    tracer = Tracer()
    tracer.configure(writer=DummyWriter())

    with tracer.trace("parent", service="top_level_test") as parent:
        with tracer.trace("child") as child:
            pass

    assert parent.get_metric("_dd.top_level") == 1
    assert "_dd.top_level" not in child.get_metrics()


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

    trace = [Span("span1"), Span("span2"), Span("span3")]
    # Test return value contains all spans in the argument
    assert trace_processors.process_trace(trace[:]) == trace


def test_span_truncator():
    """TruncateSpanProcessor truncates information in spans"""
    span = Span("span1", resource="x" * (MAX_RESOURCE_NAME_LENGTH + 10))
    span.set_metric("m" * (MAX_METRIC_KEY_LENGTH + 10), 1)
    span.set_tag("t" * (MAX_META_KEY_LENGTH + 10), "v" * (MAX_META_VALUE_LENGTH + 10))

    TruncateSpanProcessor().on_span_finish(span)

    tags = span.get_tags()
    metrics = span.get_metrics()

    assert span.resource == "x" * MAX_RESOURCE_NAME_LENGTH
    assert tags["t" * MAX_META_KEY_LENGTH] == "v" * MAX_META_VALUE_LENGTH
    assert metrics["m" * MAX_METRIC_KEY_LENGTH] == 1


def test_span_normalizator():
    """NormalizeSpanProcessor adds missing information to spans"""
    span = Span("", span_type="x" * (MAX_TYPE_LENGTH + 10))

    NormalizeSpanProcessor().on_span_finish(span)

    assert span.service == DEFAULT_SERVICE_NAME
    assert span.name == DEFAULT_SPAN_NAME
    assert span.resource == DEFAULT_SPAN_NAME
    assert span.span_type == "x" * MAX_TYPE_LENGTH


def test_single_span_sampling_processor():
    """Test that single span sampling tags are applied to spans that should get sampled"""

    rule_1 = SpanSamplingRule(service="test_service", name="test_name")
    rules = [rule_1]
    processor = SingleSpanSamplingProcessor(rules)
    tracer = Tracer()
    tracer.configure(writer=DummyWriter())
    tracer._span_processors.append(processor)

    span = traced_function(tracer)

    assert_sampling_decision_tags(span)


def test_single_span_sampling_processor_match_second_rule():
    """Test that single span sampling rule is applied if the first rule does not match, but a later one does"""

    rule_1 = SpanSamplingRule(service="test_service", name="test_name")
    rule_2 = SpanSamplingRule(service="test_service2", name="test_name2")
    rules = [rule_1, rule_2]
    processor = SingleSpanSamplingProcessor(rules)
    tracer = Tracer()
    tracer.configure(writer=DummyWriter())
    tracer._span_processors.append(processor)

    span = traced_function(tracer, name="test_name2", service="test_service2")

    assert_sampling_decision_tags(span)


def test_single_span_sampling_processor_rule_order_drop():
    """Test that single span sampling rules are applied in an order and
    will only be applied if earlier rules have not been
    """

    rule_1 = SpanSamplingRule(service="test_service", name="test_name", sample_rate=0)
    rule_2 = SpanSamplingRule(service="test_service", name="test_name", sample_rate=1.0)
    rules = [rule_1, rule_2]
    processor = SingleSpanSamplingProcessor(rules)
    tracer = Tracer()
    tracer.configure(writer=DummyWriter())
    tracer._span_processors.append(processor)

    span = traced_function(tracer)

    assert_sampling_decision_tags(span, sample_rate=None, mechanism=None, limit=None)


def test_single_span_sampling_processor_rule_order_keep():
    """Test that single span sampling rules are applied in an order
    and will not be applied if an earlier rule has been
    """

    rule_1 = SpanSamplingRule(service="test_service", name="test_name", sample_rate=1.0)
    rule_2 = SpanSamplingRule(service="test_service", name="test_name", sample_rate=0)
    rules = [rule_1, rule_2]
    processor = SingleSpanSamplingProcessor(rules)
    tracer = Tracer()
    tracer.configure(writer=DummyWriter())
    tracer._span_processors.append(processor)

    span = traced_function(tracer)

    assert_sampling_decision_tags(span)


def test_single_span_sampling_processor_do_not_tag_if_tracer_samples():
    """Test that single span sampling rules aren't applied if a span is already going to be sampled by trace sampler"""

    rule_1 = SpanSamplingRule(service="test_service", name="test_name")
    rules = [rule_1]
    processor = SingleSpanSamplingProcessor(rules)
    tracer = Tracer()
    tracer.configure(writer=DummyWriter())
    tracer._span_processors.append(processor)

    span = traced_function(tracer, trace_sampling=True)

    assert_sampling_decision_tags(span, sample_rate=None, mechanism=None, limit=None)


def traced_function(tracer, name="test_name", service="test_service", trace_sampling=False):
    with tracer.trace(name) as span:
        # If the trace sampler samples the trace, then we shouldn't add the span sampling tags
        if trace_sampling:
            span.context.sampling_priority = 1
        else:
            span.context.sampling_priority = 0

        span.service = service
    return span


def assert_sampling_decision_tags(
    span, sample_rate=1.0, mechanism=SamplingMechanism.SPAN_SAMPLING_RULE, limit=None, trace_sampling=False
):
    assert span.get_metric(_SINGLE_SPAN_SAMPLING_RATE) == sample_rate
    assert span.get_metric(_SINGLE_SPAN_SAMPLING_MECHANISM) == mechanism
    assert span.get_metric(_SINGLE_SPAN_SAMPLING_MAX_PER_SEC) == limit

    if trace_sampling:
        assert span.get_metric(SAMPLING_PRIORITY_KEY) >= 0
