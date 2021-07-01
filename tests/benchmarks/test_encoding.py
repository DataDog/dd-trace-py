import random

import msgpack
from msgpack.fallback import Packer
import pytest

from ddtrace.internal.encoding import MsgpackEncoder
from ddtrace.internal.encoding import _EncoderBase
from tests.tracer.test_encoders import RefMsgpackEncoder
from tests.tracer.test_encoders import gen_trace


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


# Set a static seed to ensure gen_trace is consistant
random.seed(13)
trace_large = gen_trace(nspans=1000, key_size=35, ntags=55, nmetrics=24)
trace_medium = gen_trace(nspans=500, key_size=25, ntags=30, nmetrics=15)
trace_small = gen_trace(nspans=50, key_size=10, ntags=5, nmetrics=4)
trace_small_no_tags = gen_trace(nspans=50, key_size=10, ntags=0, nmetrics=0)
trace_small_one_tag = gen_trace(nspans=50, key_size=10, ntags=1, nmetrics=0)
trace_small_complex = gen_trace(nspans=50, key_size=10, ntags=50, nmetrics=25)


# group name: encoding
@pytest.mark.benchmark(group="encoding", min_time=0.005)
def test_encode_1000_span_trace(benchmark):
    benchmark(msgpack_encoder.encode_trace, trace_large)


@pytest.mark.benchmark(group="encoding", min_time=0.005)
def test_encode_500_span_trace(benchmark):
    benchmark(msgpack_encoder.encode_trace, trace_medium)


@pytest.mark.benchmark(group="encoding", min_time=0.005)
def test_encode_1000_span_trace_fallback(benchmark):
    encoder = PPMsgpackEncoder()
    benchmark(encoder.encode_trace, trace_large)


@pytest.mark.benchmark(group="encoding", min_time=0.005)
def test_encode_500_span_trace_fallback(benchmark):
    encoder = PPMsgpackEncoder()
    benchmark(encoder.encode_trace, trace_medium)


@pytest.mark.benchmark(group="encoding", min_time=0.005)
def test_encode_1000_span_trace_custom(benchmark):
    benchmark(trace_encoder.encode_trace, trace_large)


@pytest.mark.benchmark(group="encoding", min_time=0.005)
def test_encode_500_span_trace_custom(benchmark):
    benchmark(trace_encoder.encode_trace, trace_medium)


# group name: encoding.small
@pytest.mark.benchmark(group="encoding.small", min_time=0.005)
def test_encode_trace_small(benchmark):
    benchmark(msgpack_encoder.encode_trace, trace_small)


@pytest.mark.benchmark(group="encoding.small", min_time=0.005)
def test_encode_trace_small_complex(benchmark):
    benchmark(msgpack_encoder.encode_trace, trace_small_complex)


@pytest.mark.benchmark(group="encoding.small", min_time=0.005)
def test_encode_trace_small_no_tags(benchmark):
    benchmark(msgpack_encoder.encode_trace, trace_small_no_tags)


@pytest.mark.benchmark(group="encoding.small", min_time=0.005)
def test_encode_trace_small_one_tag(benchmark):
    benchmark(msgpack_encoder.encode_trace, trace_small_one_tag)


@pytest.mark.benchmark(group="encoding.small", min_time=0.005)
def test_encode_trace_small_custom(benchmark):
    benchmark(trace_encoder.encode_trace, trace_small)


@pytest.mark.benchmark(group="encoding.small", min_time=0.005)
def test_encode_trace_small_custom_complex(benchmark):
    benchmark(trace_encoder.encode_trace, trace_small_complex)


# group name: encoding.small.multi
@pytest.mark.benchmark(group="encoding.small.multi", min_time=0.005)
def test_encode_trace_small_multi(benchmark):
    benchmark(msgpack_encoder.encode_traces, [trace_small for _ in range(50)])


@pytest.mark.benchmark(group="encoding.small.multi", min_time=0.005)
def test_encode_trace_small_multi_custom(benchmark):
    benchmark(trace_encoder.encode_traces, [trace_small for _ in range(50)])


# group name: encoding.join-encoded
@pytest.mark.benchmark(group="encoding.join_encoded", min_time=0.005)
def test_join_encoded(benchmark):
    benchmark(
        msgpack_encoder.join_encoded,
        [
            msgpack_encoder.encode_trace(trace_large),
            msgpack_encoder.encode_trace(trace_medium),
            msgpack_encoder.encode_trace(trace_small),
        ],
    )


@pytest.mark.benchmark(group="encoding.join_encoded", min_time=0.005)
def test_join_encoded_custom(benchmark):
    benchmark(
        trace_encoder.join_encoded,
        [
            trace_encoder.encode_trace(trace_large),
            msgpack_encoder.encode_trace(trace_medium),
            trace_encoder.encode_trace(trace_small),
        ],
    )
