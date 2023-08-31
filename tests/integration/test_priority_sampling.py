import time

from ddtrace import Tracer
from ddtrace.constants import AUTO_KEEP
from ddtrace.constants import SAMPLING_PRIORITY_KEY
from ddtrace.internal.encoding import JSONEncoder
from ddtrace.internal.encoding import MsgpackEncoderV03 as Encoder
from ddtrace.internal.writer import AgentWriter
from tests.integration.utils import parametrize_with_all_encodings
from tests.integration.utils import skip_if_testagent
from tests.utils import override_global_config


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

    tracer._writer.spans = []
    tracer._writer.traces = []
    tracer._writer.json_encoder = JSONEncoder()
    tracer._writer.msgpack_encoder = Encoder(4 << 20, 4 << 20)
    tracer._writer.write = monkeypatched_write.__get__(tracer._writer, AgentWriter)


def _prime_tracer_with_priority_sample_rate_from_agent(t, service, env):
    # Send the data once because the agent doesn't respond with them on the
    # first payload.
    s = t.trace("operation", service=service)
    s.finish()
    t.flush()

    sampler_key = "service:{},env:{}".format(service, env)
    while sampler_key not in t._writer._priority_sampler._by_service_samplers:
        time.sleep(1)
        s = t.trace("operation", service=service)
        s.finish()
        t.flush()


@parametrize_with_all_encodings
@skip_if_testagent
def test_priority_sampling_rate_honored(encoding, monkeypatch):
    monkeypatch.setenv("DD_TRACE_API_VERSION", encoding)

    _id = time.time()
    env = "my-env-{}".format(_id)
    with override_global_config(dict(env=env)):
        service = "my-svc-{}".format(_id)
        t = Tracer()

        # send a ton of traces from different services to make the agent adjust its sample rate for ``service,env``
        for i in range(100):
            s = t.trace("operation", service="dummysvc{}".format(i))
            s.finish()
        t.flush()

        _prime_tracer_with_priority_sample_rate_from_agent(t, service, env)
        sampler_key = "service:{},env:{}".format(service, env)
        assert sampler_key in t._writer._priority_sampler._by_service_samplers

        rate_from_agent = t._writer._priority_sampler._by_service_samplers[sampler_key].sample_rate
        assert 0 < rate_from_agent < 1

        _turn_tracer_into_dummy(t)
        captured_span_count = 100
        for _ in range(captured_span_count):
            with t.trace("operation", service=service) as s:
                pass
            t.flush()
        assert len(t._writer.traces) == captured_span_count
        sampled_spans = [s for s in t._writer.spans if s.context._metrics[SAMPLING_PRIORITY_KEY] == AUTO_KEEP]
        sampled_ratio = len(sampled_spans) / captured_span_count
        diff_magnitude = abs(sampled_ratio - rate_from_agent)
        assert (
            diff_magnitude < 0.3
        ), "the proportion of sampled spans should approximate the sample rate given by the agent"

        t.shutdown()


@parametrize_with_all_encodings
@skip_if_testagent
def test_priority_sampling_response(encoding, monkeypatch):
    monkeypatch.setenv("DD_TRACE_API_VERSION", encoding)

    _id = time.time()
    env = "my-env-{}".format(_id)
    with override_global_config(dict(env=env)):
        service = "my-svc-{}".format(_id)
        sampler_key = "service:{},env:{}".format(service, env)
        t = Tracer()
        assert sampler_key not in t._writer._priority_sampler._by_service_samplers
        _prime_tracer_with_priority_sample_rate_from_agent(t, service, env)
        assert (
            sampler_key in t._writer._priority_sampler._by_service_samplers
        ), "after fetching priority sample rates from the agent, the tracer should hold those rates"
        t.shutdown()
