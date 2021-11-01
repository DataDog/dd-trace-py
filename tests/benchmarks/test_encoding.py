import msgpack
from msgpack.fallback import Packer
import pytest

from ddtrace.ext.ci import CI_APP_TEST_ORIGIN
from ddtrace.internal.encoding import MSGPACK_ENCODERS
from ddtrace.internal.encoding import MsgpackEncoderV03
from ddtrace.internal.encoding import _EncoderBase
from tests.tracer.test_encoders import REF_MSGPACK_ENCODERS
from tests.tracer.test_encoders import gen_trace
from tests.utils import DummyTracer


def allencodings(f):
    return pytest.mark.parametrize("encoding", ["v0.3", "v0.5"])(f)


class PPMsgpackEncoder(_EncoderBase):
    content_type = "application/msgpack"

    def encode_traces(self, traces):
        normalized_traces = [[span.to_dict() for span in trace] for trace in traces]
        return self.encode(normalized_traces)

    @staticmethod
    def encode(obj):
        return Packer().pack(obj)

    @staticmethod
    def decode(data):
        return msgpack.unpackb(data, raw=True)


trace_large = gen_trace(nspans=1000)
trace_small = gen_trace(nspans=50, key_size=10, ntags=5, nmetrics=4)


@allencodings
@pytest.mark.benchmark(group="encoding", min_time=0.005)
def test_encode_1000_span_trace(benchmark, encoding):
    benchmark(REF_MSGPACK_ENCODERS[encoding]().encode_traces, [trace_large])


@allencodings
@pytest.mark.benchmark(group="encoding.small", min_time=0.005)
def test_encode_trace_small(benchmark, encoding):
    benchmark(REF_MSGPACK_ENCODERS[encoding]().encode_traces, [trace_small])


@allencodings
@pytest.mark.benchmark(group="encoding.small.multi", min_time=0.005)
def test_encode_trace_small_multi(benchmark, encoding):
    benchmark(REF_MSGPACK_ENCODERS[encoding]().encode_traces, [trace_small for _ in range(50)])


@pytest.mark.benchmark(group="encoding", min_time=0.005)
def test_encode_1000_span_trace_fallback(benchmark):
    encoder = PPMsgpackEncoder()
    benchmark(encoder.encode_traces, [trace_large])


@allencodings
@pytest.mark.benchmark(group="encoding", min_time=0.005)
def test_encode_1000_span_trace_custom(benchmark, encoding):
    trace_encoder = MSGPACK_ENCODERS[encoding](4 << 20, 4 << 20)

    def _():
        trace_encoder.put(trace_large)
        trace_encoder.encode()

    benchmark(_)


@allencodings
@pytest.mark.benchmark(group="encoding.small", min_time=0.005)
def test_encode_trace_small_custom(benchmark, encoding):
    trace_encoder = MSGPACK_ENCODERS[encoding](4 << 20, 4 << 20)

    def _():
        trace_encoder.put(trace_small)
        trace_encoder.encode()

    benchmark(_)


@allencodings
@pytest.mark.benchmark(group="encoding.small.multi", min_time=0.005)
def test_encode_trace_small_multi_custom(benchmark, encoding):
    trace_encoder = MSGPACK_ENCODERS[encoding](4 << 20, 4 << 20)

    def _():
        for _ in range(50):
            trace_encoder.put(trace_small)
        trace_encoder.encode()

    benchmark(_)


@pytest.mark.parametrize("trace_size", [1, 50, 200, 1000])
@pytest.mark.benchmark(group="encoding.dd_origin", min_time=0.005)
def test_dd_origin_tagging_spans_via_encoder(benchmark, trace_size):
    """Propagate dd_origin tags to all spans in [1, 50, 200, 1000] span trace via Encoder"""
    trace_encoder = MsgpackEncoderV03(4 << 20, 4 << 20)

    tracer = DummyTracer()
    with tracer.trace("pytest-test") as root:
        root.context.dd_origin = CI_APP_TEST_ORIGIN
        for _ in range(trace_size - 1):
            with tracer.trace("") as span:
                span.set_tag("tag", "value")
                pass
    trace = tracer.writer.pop()

    def _(trace):
        trace_encoder.put(trace)
        trace_encoder.encode()

    benchmark(_, trace)

    # Ensure encoded trace contains dd_origin tag in all spans
    trace_encoder.put(trace)
    (decoded_trace,) = trace_encoder._decode(trace_encoder.encode())
    for span in decoded_trace:
        assert span[b"meta"][b"_dd.origin"] == b"ciapp-test"
