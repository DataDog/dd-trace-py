import msgpack
from msgpack.fallback import Packer
import pytest

from ddtrace.encoding import _EncoderBase, MsgpackEncoder

from ..test_encoders import RefMsgpackEncoder, gen_trace


msgpack_encoder = RefMsgpackEncoder()
trace_encoder = MsgpackEncoder()


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


@pytest.mark.benchmark(group="encoding.join_encoded", min_time=0.005)
def test_join_encoded(benchmark):
    benchmark(
        msgpack_encoder.join_encoded,
        [msgpack_encoder.encode_trace(trace_large), msgpack_encoder.encode_trace(trace_small)],
    )


@pytest.mark.benchmark(group="encoding", min_time=0.005)
def test_encode_1000_span_trace(benchmark):
    benchmark(msgpack_encoder.encode_trace, trace_large)


@pytest.mark.benchmark(group="encoding.small", min_time=0.005)
def test_encode_trace_small(benchmark):
    benchmark(msgpack_encoder.encode_trace, trace_small)


@pytest.mark.benchmark(group="encoding.small.multi", min_time=0.005)
def test_encode_trace_small_multi(benchmark):
    benchmark(msgpack_encoder.encode_traces, [trace_small for _ in range(50)])


@pytest.mark.benchmark(group="encoding", min_time=0.005)
def test_encode_1000_span_trace_fallback(benchmark):
    encoder = PPMsgpackEncoder()
    benchmark(encoder.encode_trace, trace_large)


@pytest.mark.benchmark(group="encoding", min_time=0.005)
def test_encode_1000_span_trace_custom(benchmark):
    benchmark(trace_encoder.encode_trace, trace_large)


@pytest.mark.benchmark(group="encoding.small", min_time=0.005)
def test_encode_trace_small_custom(benchmark):
    benchmark(trace_encoder.encode_trace, trace_small)


@pytest.mark.benchmark(group="encoding.small.multi", min_time=0.005)
def test_encode_trace_small_multi_custom(benchmark):
    benchmark(trace_encoder.encode_traces, [trace_small for _ in range(50)])


@pytest.mark.benchmark(group="encoding.join_encoded", min_time=0.005)
def test_join_encoded_custom(benchmark):
    benchmark(
        trace_encoder.join_encoded, [trace_encoder.encode_trace(trace_large), trace_encoder.encode_trace(trace_small)]
    )


# import pstats, cProfile
#
# from ddtrace.encoding import TraceMsgPackEncoder
# encoder = TraceMsgPackEncoder()
# trace = gen_trace(nspans=10000)
# traces = [trace]
# cProfile.runctx("encoder.encode_traces(traces)", globals(), locals(), "Profile.prof")
#
# s = pstats.Stats("Profile.prof")
# s.strip_dirs().sort_stats("time").print_stats()
