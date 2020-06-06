import random
import string

import msgpack
from msgpack.fallback import Packer
import pytest

from ddtrace import Span, Tracer
from ddtrace.encoding import _EncoderBase, MsgpackEncoder, TraceMsgPackEncoder


msgpack_encoder = MsgpackEncoder()
trace_encoder = TraceMsgPackEncoder()


class PPMsgpackEncoder(_EncoderBase):
    content_type = "application/msgpack"

    @staticmethod
    def encode(obj):
        return Packer().pack(obj)

    @staticmethod
    def decode(data):
        if msgpack.version[:2] < (0, 6):
            return msgpack.unpackb(data)
        return msgpack.unpackb(data, raw=True)


def rands(size=6, chars=string.ascii_uppercase + string.digits):
    return "".join(random.choice(chars) for _ in range(size))


def gen_span(length=None, **span_attrs):
    # Helper to generate spans
    name = span_attrs.pop("name", None)
    if name is None:
        name = "a" * length

    span = Span(None, **span_attrs)

    for attr in span_attrs:
        if hasattr(span, attr):
            setattr(span, attr, attr)
        else:
            pass

    if length is not None:
        pass


def gen_trace(nspans=1000, ntags=50, key_size=15, value_size=20, nmetrics=10):
    t = Tracer()

    root = None
    trace = []
    for i in range(0, nspans):
        parent_id = root.span_id if root else None
        with Span(
            t,
            "span_name",
            resource="/fsdlajfdlaj/afdasd%s" % i,
            service="myservice",
            parent_id=parent_id,
        ) as span:
            span._parent = root
            span.set_tags({rands(key_size): rands(value_size) for _ in range(0, ntags)})

            # only apply a span type to the root span
            if not root:
                span.span_type = "web"

            for _ in range(0, nmetrics):
                span.set_tag(rands(key_size), random.randint(0, 2 ** 16))

            trace.append(span)

            if not root:
                root = span

    return trace


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
