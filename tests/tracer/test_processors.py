from typing import Any  # noqa:F401

import mock
import pytest

from ddtrace._trace.processor import SpanAggregator
from ddtrace._trace.processor import SpanProcessor
from ddtrace._trace.processor import TraceProcessor
from ddtrace._trace.processor import TraceSamplingProcessor
from ddtrace._trace.processor import TraceTagsProcessor
from ddtrace._trace.sampler import SamplingRule as TraceSamplingRule
from ddtrace.constants import _SAMPLING_PRIORITY_KEY
from ddtrace.constants import _SINGLE_SPAN_SAMPLING_MAX_PER_SEC
from ddtrace.constants import _SINGLE_SPAN_SAMPLING_MECHANISM
from ddtrace.constants import _SINGLE_SPAN_SAMPLING_RATE
from ddtrace.constants import AUTO_KEEP
from ddtrace.constants import AUTO_REJECT
from ddtrace.constants import MANUAL_KEEP_KEY
from ddtrace.constants import USER_KEEP
from ddtrace.constants import USER_REJECT
from ddtrace.ext import SpanTypes
from ddtrace.internal.constants import HIGHER_ORDER_TRACE_ID_BITS
from ddtrace.internal.processor.endpoint_call_counter import EndpointCallCounterProcessor
from ddtrace.internal.sampling import SamplingMechanism
from ddtrace.internal.sampling import SpanSamplingRule
from ddtrace.internal.telemetry.constants import TELEMETRY_NAMESPACE
from ddtrace.trace import Context
from ddtrace.trace import Span
from tests.utils import DummyTracer
from tests.utils import DummyWriter
from tests.utils import override_global_config


def test_no_impl():
    class BadProcessor(SpanProcessor):
        pass

    with pytest.raises(TypeError):
        BadProcessor()


def test_default_init():
    class MyProcessor(SpanProcessor):
        def on_span_start(self, span):  # type: (Span) -> None
            pass

        def on_span_finish(self, data):  # type: (Any) -> Any
            pass

    with mock.patch("ddtrace._trace.processor.log") as log:
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


@pytest.mark.subprocess(env={"DD_TRACE_PARTIAL_FLUSH_ENABLED": "true", "DD_TRACE_PARTIAL_FLUSH_MIN_SPANS": "2"})
def test_trace_top_level_span_processor_partial_flushing():
    """Parent span and child span have the same service name"""
    from ddtrace import tracer

    with tracer.trace("parent") as parent:
        with tracer.trace("1") as child1:
            pass
        with tracer.trace("2") as child2:
            pass
        with tracer.trace("3") as child3:
            pass

    # child spans 1 and 2 were partial flushed WITHOUT the parent span in the trace chunk
    assert child1.get_metric("_dd.top_level") is None
    assert child2.get_metric("_dd.top_level") is None

    # child span 3 was partial flushed WITH the parent span in the trace chunk
    assert "_dd.top_level" not in child3.get_metrics()
    assert parent.get_metric("_dd.top_level") == 1


def test_trace_top_level_span_processor_same_service_name():
    """Parent span and child span have the same service name"""

    tracer = DummyTracer()

    with tracer.trace("parent", service="top_level_test") as parent:
        with tracer.trace("child") as child:
            pass

    assert parent.get_metric("_dd.top_level") == 1
    assert "_dd.top_level" not in child.get_metrics()


def test_trace_top_level_span_processor_different_service_name():
    """Parent span and child span have the different service names"""

    tracer = DummyTracer()

    with tracer.trace("parent", service="top_level_test_service") as parent:
        with tracer.trace("child", service="top_level_test_service2") as child:
            pass

    assert parent.get_metric("_dd.top_level") == 1
    assert child.get_metric("_dd.top_level") == 1


def test_trace_top_level_span_processor_orphan_span():
    """Trace chuck does not contain parent span"""

    tracer = DummyTracer()

    with tracer.trace("parent") as parent:
        pass

    with tracer.start_span("orphan span", child_of=parent) as orphan_span:
        pass

    # top_level in orphan_span should not be set as implicitly it is false
    assert orphan_span.get_metric("_dd.top_level") is None


@pytest.mark.parametrize(
    "trace_id",
    [
        2**128 - 1,
        2**64 + 1,
        2**96 - 1,
    ],
)
def test_trace_128bit_processor(trace_id):
    """
    When 128bit trace ids are generated, ensure the TraceTagsProcessor tags stores
    the higher order bits on the chunk root span.
    """
    ctx = Context(trace_id=trace_id, span_id=2**64 - 1)
    spans = [Span("hello", trace_id=ctx.trace_id, context=ctx, parent_id=ctx.span_id) for _ in range(10)]

    spans = TraceTagsProcessor().process_trace(spans)

    chunk_root = spans[0]
    assert chunk_root.trace_id == ctx.trace_id
    assert chunk_root.trace_id >= 2**64
    assert chunk_root._meta[HIGHER_ORDER_TRACE_ID_BITS] == "{:016x}".format(chunk_root.trace_id >> 64)


def test_span_creation_metrics():
    """Test that telemetry metrics are queued in batches of 100 and the remainder is sent on shutdown"""
    writer = DummyWriter()
    aggr = SpanAggregator(partial_flush_enabled=False, partial_flush_min_spans=0, trace_processors=[], writer=writer)

    with override_global_config(dict(_telemetry_enabled=True)):
        with mock.patch("ddtrace.internal.telemetry.telemetry_writer.add_count_metric") as mock_tm:
            for _ in range(300):
                span = Span("span", on_finish=[aggr.on_span_finish])
                aggr.on_span_start(span)
                span.finish()

            span = Span("span", on_finish=[aggr.on_span_finish])
            aggr.on_span_start(span)
            span.finish()

            mock_tm.assert_has_calls(
                [
                    mock.call(
                        TELEMETRY_NAMESPACE.TRACERS, "spans_created", 100, tags=(("integration_name", "datadog"),)
                    ),
                    mock.call(
                        TELEMETRY_NAMESPACE.TRACERS, "spans_finished", 100, tags=(("integration_name", "datadog"),)
                    ),
                    mock.call(
                        TELEMETRY_NAMESPACE.TRACERS, "spans_created", 100, tags=(("integration_name", "datadog"),)
                    ),
                    mock.call(
                        TELEMETRY_NAMESPACE.TRACERS, "spans_finished", 100, tags=(("integration_name", "datadog"),)
                    ),
                    mock.call(
                        TELEMETRY_NAMESPACE.TRACERS, "spans_created", 100, tags=(("integration_name", "datadog"),)
                    ),
                    mock.call(
                        TELEMETRY_NAMESPACE.TRACERS, "spans_finished", 100, tags=(("integration_name", "datadog"),)
                    ),
                ]
            )
            mock_tm.reset_mock()
            aggr.shutdown(None)
            mock_tm.assert_has_calls(
                [
                    mock.call(TELEMETRY_NAMESPACE.TRACERS, "spans_created", 1, tags=(("integration_name", "datadog"),)),
                    mock.call(
                        TELEMETRY_NAMESPACE.TRACERS, "spans_finished", 1, tags=(("integration_name", "datadog"),)
                    ),
                ]
            )


def test_changing_tracer_sampler_changes_tracesamplingprocessor_sampler():
    """Changing the tracer sampler should change the sampling processor's sampler"""
    tracer = DummyTracer()
    # get processor
    sampling_processor = tracer._span_aggregator.sampling_processor
    assert sampling_processor.sampler is tracer._sampler

    new_sampler = mock.Mock()
    tracer._sampler = new_sampler

    assert sampling_processor.sampler is new_sampler


def test_single_span_sampling_processor():
    """Test that single span sampling tags are applied to spans that should get sampled"""
    rule_1 = SpanSamplingRule(service="test_service", name="test_name", sample_rate=1.0, max_per_second=-1)
    rules = [rule_1]
    sampling_processor = TraceSamplingProcessor(False, rules, False)
    sampling_processor.sampler.rules = [TraceSamplingRule(sample_rate=0.0)]
    tracer = DummyTracer()
    switch_out_trace_sampling_processor(tracer, sampling_processor)

    span = traced_function(tracer)

    assert_span_sampling_decision_tags(span)


def test_single_span_sampling_processor_match_second_rule():
    """Test that single span sampling rule is applied if the first rule does not match, but a later one does"""

    rule_1 = SpanSamplingRule(service="test_service", name="test_name", sample_rate=1.0, max_per_second=-1)
    rule_2 = SpanSamplingRule(service="test_service2", name="test_name2", sample_rate=1.0, max_per_second=-1)
    rules = [rule_1, rule_2]
    processor = TraceSamplingProcessor(False, rules, False)
    processor.sampler.rules = [TraceSamplingRule(sample_rate=0.0)]
    tracer = DummyTracer()
    switch_out_trace_sampling_processor(tracer, processor)

    span = traced_function(tracer, name="test_name2", service="test_service2")

    assert_span_sampling_decision_tags(span)


def test_single_span_sampling_processor_rule_order_drop():
    """Test that single span sampling rules are applied in an order and
    will only be applied if earlier rules have not been
    """

    rule_1 = SpanSamplingRule(service="test_service", name="test_name", sample_rate=0, max_per_second=-1)
    rule_2 = SpanSamplingRule(service="test_service", name="test_name", sample_rate=1.0, max_per_second=-1)
    rules = [rule_1, rule_2]
    processor = TraceSamplingProcessor(False, rules, False)
    processor.sampler.rules = [TraceSamplingRule(sample_rate=0.0)]
    tracer = DummyTracer()
    switch_out_trace_sampling_processor(tracer, processor)

    span = traced_function(tracer)

    assert_span_sampling_decision_tags(span, sample_rate=None, mechanism=None, limit=None)


def test_single_span_sampling_processor_rule_order_keep():
    """Test that single span sampling rules are applied in an order
    and will not be applied if an earlier rule has been
    """

    rule_1 = SpanSamplingRule(service="test_service", name="test_name", sample_rate=1.0, max_per_second=-1)
    rule_2 = SpanSamplingRule(service="test_service", name="test_name", sample_rate=0, max_per_second=-1)
    rules = [rule_1, rule_2]
    processor = TraceSamplingProcessor(False, rules, False)
    processor.sampler.rules = [TraceSamplingRule(sample_rate=0.0)]
    tracer = DummyTracer()
    switch_out_trace_sampling_processor(tracer, processor)

    span = traced_function(tracer)

    assert_span_sampling_decision_tags(span)


@pytest.mark.parametrize(
    "span_sample_rate_rule, expected_span_sample_rate_tag, mechanism, trace_sampling_priority",
    [
        (0, None, None, AUTO_KEEP),  # Span sample rate is 0, but the tracer is going to keep it
        (0, None, None, USER_KEEP),  # Span sample rate is 0, but the user is going to keep it
        (0, None, None, AUTO_REJECT),  # The tracer will try to drop the span, the span sampling rule will not keep it
        (0, None, None, USER_REJECT),  # The user will try to drop the span, the span sampling rule will not keep it
        # The tracer will try to drop the span, but span sampling will keep it
        (1, 1, SamplingMechanism.SPAN_SAMPLING_RULE, AUTO_REJECT),
        # The user will try to drop the span, but span sampling will keep it
        (1, 1, SamplingMechanism.SPAN_SAMPLING_RULE, USER_REJECT),
        # Span sample rate is 1, but the tracer is going to keep it so span sampling tags will not be applied
        (1, None, None, AUTO_KEEP),
        # Span sample rate is 1, but the user is going to keep it so span sampling tags will not be applied
        (1, None, None, USER_KEEP),
    ],
)
def test_single_span_sampling_processor_w_tracer_sampling(
    span_sample_rate_rule, expected_span_sample_rate_tag, mechanism, trace_sampling_priority
):
    """Test how the single span sampler interacts with the trace sampler"""

    rule_1 = SpanSamplingRule(
        service="test_service", name="test_name", sample_rate=span_sample_rate_rule, max_per_second=-1
    )
    rules = [rule_1]
    processor = TraceSamplingProcessor(False, rules, False)
    processor.sampler.rules = [TraceSamplingRule(sample_rate=0.0)]
    tracer = DummyTracer()
    switch_out_trace_sampling_processor(tracer, processor)

    span = traced_function(tracer, trace_sampling_priority=trace_sampling_priority)

    assert_span_sampling_decision_tags(
        span,
        sample_rate=expected_span_sample_rate_tag,
        mechanism=mechanism,
        trace_sampling_priority=trace_sampling_priority,
    )


def test_single_span_sampling_processor_w_tracer_sampling_after_processing():
    """Since the root span has MANUAL_KEEP_KEY set and the child span has not yet run through
    the TraceSamplingProcessor, the child span will have the manual keep in its context and therefore skip single span
    sampling. This leads to span sampling rates matching the reality of what span sampling
    is responsible for sampling.
    """
    rule_1 = SpanSamplingRule(name="child", sample_rate=1.0, max_per_second=-1)
    rules = [rule_1]
    processor = TraceSamplingProcessor(False, rules, False)
    processor.sampler.rules = [TraceSamplingRule(sample_rate=0.0)]
    tracer = DummyTracer()
    switch_out_trace_sampling_processor(tracer, processor)
    root = tracer.trace("root")

    # When trace sampling marks it as a drop
    root.context.sampling_priority = AUTO_REJECT
    assert root.context.sampling_priority <= 0

    # Child is checked against the span sampling rules, and then is kept
    child = tracer.trace("child")
    child.finish()
    tracer.flush()

    # The trace is updated to be a keep, but we already span sampled child
    root.set_tag(MANUAL_KEEP_KEY)
    root.finish()
    assert_span_sampling_decision_tags(child, None, None, None)
    assert child.context.sampling_priority == USER_KEEP


def test_single_span_sampling_processor_no_rules():
    """Test that single span sampling rules aren't applied if a span is already going to be sampled by trace sampler"""
    tracer = DummyTracer()

    span = traced_function(tracer, trace_sampling_priority=AUTO_KEEP)

    assert_span_sampling_decision_tags(
        span,
        sample_rate=None,
        mechanism=None,
        limit=None,
        trace_sampling_priority=AUTO_KEEP,
    )


def test_single_span_sampling_processor_w_stats_computation():
    """Test that span processor changes _sampling_priority_v1 to 2 when stats computation is enabled"""
    rule_1 = SpanSamplingRule(service="test_service", name="test_name", sample_rate=1.0, max_per_second=-1)
    rules = [rule_1]
    processor = TraceSamplingProcessor(False, rules, False)
    processor.sampler.rules = [TraceSamplingRule(sample_rate=0.0)]
    with override_global_config(dict(_trace_compute_stats=True)):
        tracer = DummyTracer()
        switch_out_trace_sampling_processor(tracer, processor)

        span = traced_function(tracer)

    assert_span_sampling_decision_tags(span, trace_sampling_priority=USER_KEEP)


def traced_function(tracer, name="test_name", service="test_service", trace_sampling_priority=0):
    with tracer.trace(name) as span:
        # If the trace sampler samples the trace, then we shouldn't add the span sampling tags
        span.context.sampling_priority = trace_sampling_priority

        span.service = service
    return span


def assert_span_sampling_decision_tags(
    span, sample_rate=1.0, mechanism=SamplingMechanism.SPAN_SAMPLING_RULE, limit=None, trace_sampling_priority=None
):
    assert span.get_metric(_SINGLE_SPAN_SAMPLING_RATE) == sample_rate
    assert span.get_metric(_SINGLE_SPAN_SAMPLING_MECHANISM) == mechanism
    assert span.get_metric(_SINGLE_SPAN_SAMPLING_MAX_PER_SEC) == limit

    if trace_sampling_priority:
        assert span.get_metric(_SAMPLING_PRIORITY_KEY) == trace_sampling_priority


def switch_out_trace_sampling_processor(tracer, sampling_processor):
    tracer._span_aggregator.sampling_processor = sampling_processor


def test_endpoint_call_counter_processor():
    """ProfilingSpanProcessor collects information about endpoints for profiling"""
    spanA = Span("spanA", resource="a", span_type=SpanTypes.WEB)
    spanA._local_root = spanA
    spanB = Span("spanB", resource="b", span_type=SpanTypes.WEB)
    spanB._local_root = spanB
    spanNonWeb = Span("spanNonWeb", resource="c", span_type=SpanTypes.WORKER)
    spanNonLocalRoot = Span("spanNonLocalRoot", resource="d")

    processor = EndpointCallCounterProcessor()
    processor.enable()

    processor.on_span_finish(spanA)
    processor.on_span_finish(spanA)
    processor.on_span_finish(spanB)
    processor.on_span_finish(spanNonWeb)
    processor.on_span_finish(spanNonLocalRoot)

    assert processor.reset()[0] == {"a": 2, "b": 1}
    # Make sure data has been cleared
    assert processor.reset()[0] == {}


def test_endpoint_call_counter_processor_disabled():
    """ProfilingSpanProcessor is disabled by default"""
    spanA = Span("spanA", resource="a", span_type=SpanTypes.WEB)
    spanA._local_root = spanA

    processor = EndpointCallCounterProcessor()

    processor.on_span_finish(spanA)

    assert processor.reset()[0] == {}


def test_endpoint_call_counter_processor_real_tracer():
    tracer = DummyTracer()
    tracer._endpoint_call_counter_span_processor.enable()

    with tracer.trace("parent", service="top_level_test_service", resource="a", span_type=SpanTypes.WEB):
        with tracer.trace("child", service="top_level_test_service2"):
            # Non root spans are ignores
            with tracer.trace("parent", service="top_level_test_service", resource="ignored", span_type=SpanTypes.WEB):
                pass

    with tracer.trace("parent", service="top_level_test_service", resource="a", span_type=SpanTypes.WEB):
        pass

    with tracer.trace("parent", service="top_level_test_service", resource="b", span_type=SpanTypes.WEB):
        pass

    # Non web spans are ignored
    with tracer.trace("parent", service="top_level_test_service", resource="ignored", span_type=SpanTypes.HTTP):
        pass

    assert tracer._endpoint_call_counter_span_processor.reset()[0] == {"a": 2, "b": 1}


def test_trace_tag_processor_adds_chunk_root_tags():
    tracer = DummyTracer()

    with tracer.trace("parent") as parent:
        with tracer.trace("child") as child:
            pass

    # test that parent span gets required chunk root span tags and child does not get language tag
    assert parent.get_tag("language") == "python"
    assert child.get_tag("language") is None


def test_register_unregister_span_processor():
    class TestProcessor(SpanProcessor):
        def on_span_start(self, span):
            span.set_tag("on_start", "ok")

        def on_span_finish(self, span):
            span.set_tag("on_finish", "ok")

    tp = TestProcessor()
    tp.register()

    tracer = DummyTracer()

    with tracer.trace("test") as span:
        assert span.get_tag("on_start") == "ok"
    assert span.get_tag("on_finish") == "ok"

    tp.unregister()

    with tracer.trace("test") as span:
        assert span.get_tag("on_start") is None
    assert span.get_tag("on_finish") is None
