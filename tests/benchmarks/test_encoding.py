import msgpack
from msgpack.fallback import Packer
import pytest

from ddtrace.ext.ci import CI_APP_TEST_ORIGIN
from ddtrace.internal.encoding import MsgpackEncoder
from ddtrace.internal.encoding import _EncoderBase
from tests.tracer.test_encoders import RefMsgpackEncoder
from tests.tracer.test_encoders import gen_trace
from tests.utils import DummyTracer


msgpack_encoder = RefMsgpackEncoder()
trace_encoder = MsgpackEncoder(4 << 20, 4 << 20)


class PPMsgpackEncoder(_EncoderBase):
    content_type = "application/msgpack"

    @staticmethod
    def encode(obj):
        return Packer().pack(obj)

    @staticmethod
    def decode(data):
        return msgpack.unpackb(data, raw=True)


trace_large = gen_trace(nspans=1000)
trace_small = gen_trace(nspans=50, key_size=10, ntags=5, nmetrics=4)


@pytest.mark.benchmark(group="encoding", min_time=0.005)
def test_encode_1000_span_trace(benchmark):
    benchmark(msgpack_encoder.encode_traces, [trace_large])


@pytest.mark.benchmark(group="encoding.small", min_time=0.005)
def test_encode_trace_small(benchmark):
    benchmark(msgpack_encoder.encode_traces, [trace_small])


@pytest.mark.benchmark(group="encoding.small.multi", min_time=0.005)
def test_encode_trace_small_multi(benchmark):
    benchmark(msgpack_encoder.encode_traces, [trace_small for _ in range(50)])


@pytest.mark.benchmark(group="encoding", min_time=0.005)
def test_encode_1000_span_trace_fallback(benchmark):
    encoder = PPMsgpackEncoder()
    benchmark(encoder.encode_traces, [trace_large])


@pytest.mark.benchmark(group="encoding", min_time=0.005)
def test_encode_1000_span_trace_custom(benchmark):
    def _():
        trace_encoder.put(trace_large)
        trace_encoder.encode()

    benchmark(_)


@pytest.mark.benchmark(group="encoding.small", min_time=0.005)
def test_encode_trace_small_custom(benchmark):
    def _():
        trace_encoder.put(trace_small)
        trace_encoder.encode()

    benchmark(_)


@pytest.mark.benchmark(group="encoding.small.multi", min_time=0.005)
def test_encode_trace_small_multi_custom(benchmark):
    def _():
        for _ in range(50):
            trace_encoder.put(trace_small)
        trace_encoder.encode()

    benchmark(_)


@pytest.mark.parametrize("trace_size", [1, 50, 200, 1000])
@pytest.mark.benchmark(group="encoding.dd_origin", min_time=0.005)
def test_dd_origin_tagging_spans_via_encoder(benchmark, trace_size):
    """Propagate dd_origin tags to all spans in [1, 50, 200, 1000] span trace via Encoder"""
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
