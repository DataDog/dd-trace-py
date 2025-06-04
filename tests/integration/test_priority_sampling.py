import time

import pytest

from ddtrace.constants import AUTO_KEEP
from ddtrace.constants import AUTO_REJECT
from ddtrace.internal.encoding import JSONEncoder
from ddtrace.internal.encoding import MsgpackEncoderV04 as Encoder
from ddtrace.internal.writer import AgentWriter
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
    tracer._span_aggregator.writer.write = monkeypatched_write.__get__(tracer._span_aggregator.writer, AgentWriter)


def _prime_tracer_with_priority_sample_rate_from_agent(t, service):
    # Send the data once because the agent doesn't respond with them on the
    # first payload.
    s = t.trace("operation", service=service)
    s.finish()
    t.flush()

    sampler_key = "service:{},env:".format(service)
    while sampler_key not in t._span_aggregator.sampling_processor.sampler._by_service_samplers:
        time.sleep(1)
        s = t.trace("operation", service=service)
        s.finish()
        t.flush()


@skip_if_testagent
@parametrize_with_all_encodings()
def test_priority_sampling_rate_honored(DD_TRACE_API_VERSION):
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
    assert sampler_key in t._span_aggregator.sampling_processor.sampler._by_service_samplers

    rate_from_agent = t._span_aggregator.sampling_processor.sampler._by_service_samplers[sampler_key].sample_rate
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
def test_priority_sampling_response(DD_TRACE_API_VERSION):
    import time

    from ddtrace.trace import tracer as t
    from tests.integration.test_priority_sampling import _prime_tracer_with_priority_sample_rate_from_agent

    _id = time.time()
    service = "my-svc-{}".format(_id)
    sampler_key = "service:{},env:".format(service)
    assert sampler_key not in t._span_aggregator.sampling_processor.sampler._by_service_samplers
    _prime_tracer_with_priority_sample_rate_from_agent(t, service)
    assert (
        sampler_key in t._span_aggregator.sampling_processor.sampler._by_service_samplers
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
@parametrize_with_all_encodings()
def test_priority_rate_honored_tracer_configure(DD_TRACE_API_VERSION):
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

    assert len(t._span_aggregator.sampling_processor.sampler._by_service_samplers)

    class CustomFilter(TraceFilter):
        def process_trace(self, trace):
            for span in trace:
                if span.name == "some_name":
                    return None
            return trace

    t.configure(trace_processors=[CustomFilter()])  # Triggers AgentWriter recreate
    assert len(t._span_aggregator.sampling_processor.sampler._by_service_samplers)


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
