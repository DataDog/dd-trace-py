import struct

import msgpack
from msgpack.fallback import Packer
import pytest

from ddtrace.internal.encoding import MsgpackEncoder
from ddtrace.internal.encoding import _EncoderBase
from tests.tracer.test_encoders import RefMsgpackEncoder
from tests.tracer.test_encoders import gen_trace


class PPMsgpackEncoder(_EncoderBase):
    content_type = "application/msgpack"

    @staticmethod
    def encode(obj):
        return Packer().pack(obj)

    @staticmethod
    def decode(data):
        return msgpack.unpackb(data, raw=True)

    def join_encoded(self, objs):
        """Join a list of encoded objects together as a msgpack array"""
        buf = b"".join(objs)

        count = len(objs)
        if count <= 0xF:
            return struct.pack("B", 0x90 + count) + buf
        elif count <= 0xFFFF:
            return struct.pack(">BH", 0xDC, count) + buf
        else:
            return struct.pack(">BI", 0xDD, count) + buf


msgpack_encoder = RefMsgpackEncoder()
trace_encoder = MsgpackEncoder()
fallback_encoder = PPMsgpackEncoder()

trace_large = gen_trace(nspans=1000)
trace_small = gen_trace(nspans=50, key_size=10, ntags=5, nmetrics=4)


@pytest.mark.benchmark(group="encoding.join_encoded", min_time=0.005)
@pytest.mark.parametrize(
    "encoder_name,encoder",
    [
        ("reference", msgpack_encoder),
        ("custom", trace_encoder),
        ("fallback", fallback_encoder),
    ],
)
def test_join_encoded(benchmark, encoder_name, encoder):
    traces = [encoder.encode_trace(trace_large), encoder.encode_trace(trace_small)]
    benchmark(encoder.join_encoded, traces)


@pytest.mark.benchmark(group="encoding.1000_spans", min_time=0.005)
@pytest.mark.parametrize(
    "encoder_name,encoder",
    [
        ("reference", msgpack_encoder),
        ("custom", trace_encoder),
        ("fallback", fallback_encoder),
    ],
)
def test_encode_1000_span_trace(benchmark, encoder_name, encoder):
    benchmark(encoder.encode_trace, trace_large)


@pytest.mark.benchmark(group="encoding.small", min_time=0.005)
@pytest.mark.parametrize(
    "encoder_name,encoder",
    [
        ("reference", msgpack_encoder),
        ("custom", trace_encoder),
        ("fallback", fallback_encoder),
    ],
)
def test_encode_trace_small(benchmark, encoder_name, encoder):
    benchmark(encoder.encode_trace, trace_small)


@pytest.mark.benchmark(group="encoding.small.multi", min_time=0.005)
@pytest.mark.parametrize(
    "encoder_name,encoder",
    [
        ("reference", msgpack_encoder),
        ("custom", trace_encoder),
        ("fallback", fallback_encoder),
    ],
)
def test_encode_trace_small_multi(benchmark, encoder_name, encoder):
    benchmark(encoder.encode_traces, [trace_small for _ in range(50)])


@pytest.mark.benchmark(group="encoding.small.dd_origin", min_time=0.005)
@pytest.mark.parametrize(
    "encoder_name,encoder,dd_origin",
    [
        ("reference", msgpack_encoder, "synthetics"),
        ("custom", trace_encoder, "synthetics"),
        ("fallback", fallback_encoder, "synthetics"),
    ],
)
def test_encode_trace_small_dd_origin(benchmark, encoder_name, encoder, dd_origin):
    trace = gen_trace(nspans=50, key_size=10, ntags=5, nmetrics=4, dd_origin=dd_origin)
    benchmark(encoder.encode_trace, trace)


@pytest.mark.benchmark(group="encoding.small.multi.dd_origin", min_time=0.005)
@pytest.mark.parametrize(
    "encoder_name,encoder,dd_origin",
    [
        ("reference", msgpack_encoder, "synthetics"),
        ("custom", trace_encoder, "synthetics"),
        ("fallback", fallback_encoder, "synthetics"),
    ],
)
def test_encode_trace_small_multi_dd_origin(benchmark, encoder_name, encoder, dd_origin):
    trace = gen_trace(nspans=50, key_size=10, ntags=5, nmetrics=4, dd_origin=dd_origin)
    benchmark(encoder.encode_traces, [trace for _ in range(50)])


@pytest.mark.benchmark(group="encoding.1000_spans.dd_origin", min_time=0.005)
@pytest.mark.parametrize(
    "encoder_name,encoder,dd_origin",
    [
        ("reference", msgpack_encoder, "synthetics"),
        ("custom", trace_encoder, "synthetics"),
        ("fallback", fallback_encoder, "synthetics"),
    ],
)
def test_encode_1000_span_trace_dd_origin(benchmark, encoder_name, encoder, dd_origin):
    trace = gen_trace(nspans=1000, dd_origin=dd_origin)
    benchmark(encoder.encode_trace, trace)
