import time

import pytest

from ddtrace import config
from ddtrace.constants import AUTO_KEEP
from ddtrace.constants import AUTO_REJECT
from ddtrace.internal.encoding import JSONEncoder
from ddtrace.internal.encoding import MsgpackEncoderV04 as Encoder
from ddtrace.internal.writer import AgentWriter
from ddtrace.internal.writer import NativeWriter
from ddtrace.trace import tracer as ddtracer
from tests.integration.utils import AGENT_VERSION
from tests.integration.utils import parametrize_with_all_encodings
from tests.integration.utils import skip_if_testagent


def _turn_tracer_into_dummy(tracer):
    """Override tracer's writer's write() method to keep traces instead of sending them away"""

    def monkeypatched_write(self, spans=None):
        if spans:
            traces = [spans]
            self.json_encoder.encode_traces(traces)
            self.msgpack_encoder.put(spans)
            self.msgpack_encoder.encode()
            self.spans += spans
            self.traces += traces

    tracer._span_aggregator.writer.spans = []
    tracer._span_aggregator.writer.traces = []
    tracer._span_aggregator.writer.json_encoder = JSONEncoder()
    tracer._span_aggregator.writer.msgpack_encoder = Encoder(4 << 20, 4 << 20)
    tracer._span_aggregator.writer.write = monkeypatched_write.__get__(
        tracer._span_aggregator.writer, NativeWriter if config._trace_writer_native else AgentWriter
    )


def _prime_tracer_with_priority_sample_rate_from_agent(t, service):
    # Send the data once because the agent doesn't respond with them on the
    # first payload.
    s = t.trace("operation", service=service)
    s.finish()
    t.flush()

    sampler_key = "service:{},env:".format(service)
    while sampler_key not in t._span_aggregator.sampling_processor.sampler._agent_based_samplers:
        time.sleep(1)
        s = t.trace("operation", service=service)
        s.finish()
        t.flush()


@skip_if_testagent
@parametrize_with_all_encodings()
def test_priority_sampling_rate_honored():
    import time

    from ddtrace.constants import _SAMPLING_PRIORITY_KEY  # noqa
    from ddtrace.constants import AUTO_KEEP
    from ddtrace.trace import tracer as t
    from tests.integration.test_priority_sampling import _prime_tracer_with_priority_sample_rate_from_agent
    from tests.integration.test_priority_sampling import _turn_tracer_into_dummy

    _id = time.time()
    service = "my-svc-{}".format(_id)

    # send a ton of traces from different services to make the agent adjust its sample rate for ``service,env``
    for i in range(100):
        s = t.trace("operation", service="dummysvc{}".format(i))
        s.finish()
    t.flush()

    _prime_tracer_with_priority_sample_rate_from_agent(t, service)
    sampler_key = "service:{},env:".format(service)
    assert sampler_key in t._span_aggregator.sampling_processor.sampler._agent_based_samplers

    rate_from_agent = t._span_aggregator.sampling_processor.sampler._agent_based_samplers[sampler_key].sample_rate
    assert 0 < rate_from_agent < 1

    _turn_tracer_into_dummy(t)
    captured_span_count = 100
    for _ in range(captured_span_count):
        with t.trace("operation", service=service) as s:
            pass
        t.flush()
    assert len(t._span_aggregator.writer.traces) == captured_span_count
    sampled_spans = [
        s for s in t._span_aggregator.writer.spans if s.context._metrics[_SAMPLING_PRIORITY_KEY] == AUTO_KEEP
    ]
    sampled_ratio = len(sampled_spans) / captured_span_count
    diff_magnitude = abs(sampled_ratio - rate_from_agent)
    assert diff_magnitude < 0.3, "the proportion of sampled spans should approximate the sample rate given by the agent"

    t.shutdown()


@skip_if_testagent
@parametrize_with_all_encodings()
def test_priority_sampling_response():
    import time

    from ddtrace.trace import tracer as t
    from tests.integration.test_priority_sampling import _prime_tracer_with_priority_sample_rate_from_agent

    _id = time.time()
    service = "my-svc-{}".format(_id)
    sampler_key = "service:{},env:".format(service)
    assert sampler_key not in t._span_aggregator.sampling_processor.sampler._agent_based_samplers
    _prime_tracer_with_priority_sample_rate_from_agent(t, service)
    assert (
        sampler_key in t._span_aggregator.sampling_processor.sampler._agent_based_samplers
    ), "after fetching priority sample rates from the agent, the tracer should hold those rates"
    t.shutdown()


@pytest.mark.skipif(AGENT_VERSION != "testagent", reason="Tests only compatible with a testagent")
@pytest.mark.snapshot(agent_sample_rate_by_service={"service:test,env:": 0.9999})
def test_agent_sample_rate_keep():
    """Ensure that the agent sample rate is respected when a trace is auto sampled."""
    # First trace won't actually have the sample rate applied since the response has not yet been received.
    with ddtracer.trace(""):
        pass
    # Force a flush to get the response back.
    ddtracer.flush()

    # Subsequent traces should have the rate applied.
    with ddtracer.trace("test", service="test") as span:
        pass
    ddtracer.flush()
    assert span.get_metric("_dd.agent_psr") == pytest.approx(0.9999)
    assert span.get_metric("_sampling_priority_v1") == AUTO_KEEP
    assert span.get_tag("_dd.p.dm") == "-1"


@skip_if_testagent
@parametrize_with_all_encodings(
    env={
        "DD_TRACE_SAMPLING_RULES": '[{"sample_rate": 0.1, "service": "moon"}]',
        "DD_SPAN_SAMPLING_RULES": '[{"service":"xyz", "sample_rate":0.23}]',
    }
)
def test_sampling_configurations_are_not_reset_on_tracer_configure():
    import time

    from ddtrace.trace import TraceFilter
    from ddtrace.trace import tracer as t
    from tests.integration.test_priority_sampling import _prime_tracer_with_priority_sample_rate_from_agent

    _id = time.time()
    service = "my-svc-{}".format(_id)

    # send a ton of traces from different services to make the agent adjust its sample rate for ``service,env``
    for i in range(100):
        s = t.trace("operation", service="dummysvc{}".format(i))
        s.finish()
    t.flush()

    _prime_tracer_with_priority_sample_rate_from_agent(t, service)

    agent_based_samplers = t._span_aggregator.sampling_processor.sampler._agent_based_samplers
    trace_sampling_rules = t._span_aggregator.sampling_processor.sampler.rules
    single_span_sampling_rules = t._span_aggregator.sampling_processor.single_span_rules
    assert (
        agent_based_samplers and trace_sampling_rules and single_span_sampling_rules
    ), "Expected agent sampling rules, span sampling rules, trace sampling rules to be set"
    f", got {agent_based_samplers}, {trace_sampling_rules}, {single_span_sampling_rules}"

    class CustomFilter(TraceFilter):
        def process_trace(self, trace):
            for span in trace:
                if span.name == "some_name":
                    return None
            return trace

    t.configure(trace_processors=[CustomFilter()])  # Triggers AgentWriter recreate
    assert (
        t._span_aggregator.sampling_processor.sampler._agent_based_samplers == agent_based_samplers
    ), f"Expected agent sampling rules to be set to {agent_based_samplers}, "
    f"got {t._span_aggregator.sampling_processor.sampler._agent_based_samplers}"

    assert (
        t._span_aggregator.sampling_processor.sampler.rules == trace_sampling_rules
    ), f"Expected trace sampling rules to be set to {trace_sampling_rules}, "
    f"got {t._span_aggregator.sampling_processor.sampler.rules}"

    assert len(t._span_aggregator.sampling_processor.single_span_rules) == len(
        single_span_sampling_rules
    ), f"Expected single span sampling rules to be set to {single_span_sampling_rules}, "
    f"got {t._span_aggregator.sampling_processor.single_span_rules}"


@pytest.mark.skipif(AGENT_VERSION != "testagent", reason="Tests only compatible with a testagent")
@pytest.mark.snapshot(agent_sample_rate_by_service={"service:test,env:": 0.0001})
def test_agent_sample_rate_reject():
    """Ensure that the agent sample rate is respected when a trace is auto rejected."""
    # First trace won't actually have the sample rate applied since the response has not yet been received.
    with ddtracer.trace(""):
        pass

    # Force a flush to get the response back.
    ddtracer.flush()

    # Subsequent traces should have the rate applied.
    with ddtracer.trace("test", service="test") as span:
        pass
    ddtracer.flush()
    assert span.get_metric("_dd.agent_psr") == pytest.approx(0.0001)
    assert span.get_metric("_sampling_priority_v1") == AUTO_REJECT
    assert span.get_tag("_dd.p.dm") == "-1"


@pytest.mark.skipif(AGENT_VERSION != "testagent", reason="Tests only compatible with a testagent")
@pytest.mark.snapshot()
@pytest.mark.subprocess(
    env={
        "DD_SERVICE": "animals",
        "_DD_TRACE_WRITER_NATIVE": "false",
        "DD_TRACE_SAMPLING_RULES": '[{"sample_rate": 0, "service": "animals"}]',
        "DD_SPAN_SAMPLING_RULES": '[{"service":"animals", "name":"monkey", "sample_rate":1}]',
    },
    parametrize={"DD_TRACE_COMPUTE_STATS": ["false", "true"]},
)
def test_single_span_and_trace_sampling_snapshot():
    from ddtrace import config
    from ddtrace.trace import tracer

    with tracer.trace("non_monkey") as span1:
        with tracer.trace("monkey") as span2:
            with tracer.trace("human_monkey") as span3:
                pass
        with tracer.trace("donkey_monkey") as span4:
            pass
    tracer.flush()

    # Trace level sampling decision tags should be the same.
    for span in [span1, span2, span3, span4]:
        assert span.context.sampling_priority == -1, repr(span)
        assert span.context._meta.get("_dd.p.dm") == "-3", repr(span)

    # Span 1 was sampled via trace sampling rule
    assert span1.get_metric("_dd.rule_psr") == 0
    # Span 2 was sampled via single span sampling rule
    assert span2.get_metric("_dd.span_sampling.mechanism") == 8
    assert span2.get_metric("_dd.span_sampling.rule_rate") == 1
    assert "_dd.rule_psr" not in span2.get_metrics()
    if config._trace_compute_stats:
        # If stats computation is enabled, the span sampling priority should be 2
        # Not sure why this is the case, but it's the legacy behavior.
        assert span2.get_metric("_sampling_priority_v1") == 2, repr(span2)
    else:
        assert "_sampling_priority_v1" not in span2.get_metrics(), repr(span2)
