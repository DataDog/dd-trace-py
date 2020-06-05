import random
import string

import pytest

from ddtrace import Span, Tracer


from ddtrace.encoding import _EncoderBase
import msgpack
from msgpack.fallback import Packer

class PPMsgpackEncoder(_EncoderBase):
    content_type = 'application/msgpack'

    @staticmethod
    def encode(obj):
        return Packer().pack(obj)

    @staticmethod
    def decode(data):
        if msgpack.version[:2] < (0, 6):
            return msgpack.unpackb(data)
        return msgpack.unpackb(data, raw=True)

    @staticmethod
    def join_encoded(objs):
        """Join a list of encoded objects together as a msgpack array"""
        buf = b''.join(objs)

        # Prepend array header to buffer
        # https://github.com/msgpack/msgpack-python/blob/f46523b1af7ff2d408da8500ea36a4f9f2abe915/msgpack/fallback.py#L948-L955
        count = len(objs)
        if count <= 0xf:
            return struct.pack('B', 0x90 + count) + buf
        elif count <= 0xffff:
            return struct.pack('>BH', 0xdc, count) + buf
        else:
            return struct.pack('>BI', 0xdd, count) + buf


def rands(size=6, chars=string.ascii_uppercase + string.digits):
    return ''.join(random.choice(chars) for _ in range(size))


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
        with Span(t, "span_name", resource="/fsdlajfdlaj/afdasd%s" % i, service="myservice", parent_id=parent_id, span_type="web") as span:
            span._parent = root
            span.set_tags({
                rands(key_size): rands(value_size) for _ in range(0, ntags)
            })

            for _ in range(0, nmetrics):
                span.set_tag(rands(key_size), random.randint(0, 2**16))

            trace.append(span)

            if not root:
                root = span

    return trace


'''
@pytest.mark.benchmark(group="encoding", min_time=0.005)
def test_encode_10000_span_trace(benchmark):
    from ddtrace.encoding import MsgpackEncoder
    trace = gen_trace(nspans=10000)
    encoder = MsgpackEncoder()

    benchmark(encoder.encode_trace, trace)


@pytest.mark.benchmark(group="encoding", min_time=0.005)
def test_encode_5000_span_trace(benchmark):
    from ddtrace.encoding import MsgpackEncoder
    trace = gen_trace(nspans=5000)
    encoder = MsgpackEncoder()

    benchmark(encoder.encode_trace, trace)


@pytest.mark.benchmark(group="encoding", min_time=0.005)
def test_encode_2000_span_trace(benchmark):
    from ddtrace.encoding import MsgpackEncoder
    trace = gen_trace(nspans=2000)
    encoder = MsgpackEncoder()

    benchmark(encoder.encode_trace, trace)

 
@pytest.mark.benchmark(group="encoding", min_time=0.005)
def test_encode_1000_span_2_traces(benchmark):
    from ddtrace.encoding import MsgpackEncoder
    trace = gen_trace(nspans=1000)
    trace2 = gen_trace(nspans=1000)
    encoder = MsgpackEncoder()

    benchmark(encoder.encode_traces, [trace, trace2])
'''

trace = gen_trace(nspans=1000)
trace_small = gen_trace(nspans=50, key_size=10, ntags=5, nmetrics=4)


@pytest.mark.benchmark(group="encoding.join_encoded", min_time=0.005)
def test_join_encoded(benchmark):
    from ddtrace.encoding import MsgpackEncoder
    encoder = MsgpackEncoder()

    benchmark(encoder.join_encoded, [encoder.encode_trace(trace), encoder.encode_trace(trace_small)])


@pytest.mark.benchmark(group="encoding", min_time=0.005)
def test_encode_1000_span_trace(benchmark):
    from ddtrace.encoding import MsgpackEncoder
    encoder = MsgpackEncoder()

    benchmark(encoder.encode_trace, trace)


@pytest.mark.benchmark(group="encoding.small", min_time=0.005)
def test_encode_trace_small(benchmark):
    from ddtrace.encoding import MsgpackEncoder
    encoder = MsgpackEncoder()

    benchmark(encoder.encode_trace, trace_small)


@pytest.mark.benchmark(group="encoding", min_time=0.005)
def test_encode_1000_span_trace_fallback(benchmark):
    encoder = PPMsgpackEncoder()

    benchmark(encoder.encode_trace, trace)


@pytest.mark.benchmark(group="encoding", min_time=0.005)
def test_encode_1000_span_trace_custom(benchmark):
    from ddtrace.encoding import TraceMsgPackEncoder
    encoder = TraceMsgPackEncoder()

    benchmark(encoder.encode_trace, trace)


@pytest.mark.benchmark(group="encoding.small", min_time=0.005)
def test_encode_trace_small_custom(benchmark):
    from ddtrace.encoding import TraceMsgPackEncoder
    encoder = TraceMsgPackEncoder()

    benchmark(encoder.encode_trace, trace_small)


@pytest.mark.benchmark(group="encoding.join_encoded", min_time=0.005)
def test_join_encoded_custom(benchmark):
    from ddtrace.encoding import TraceMsgPackEncoder
    encoder = TraceMsgPackEncoder()

    benchmark(encoder.join_encoded, [encoder.encode_trace(trace), encoder.encode_trace(trace_small)])


'''

@pytest.mark.benchmark(group="encoding", min_time=0.005)
def test_encode_1000_span_trace_to_dict(benchmark):
    from ddtrace.encoding import TraceMsgPackEncoder
    traces = [gen_trace(nspans=1000)]
    encoder = TraceMsgPackEncoder()

    @benchmark
    def fn():
        d = []
        for t in traces:
            d.append([s.to_dict() for s in t])


@pytest.mark.benchmark(group="encoding", min_time=0.005)
def test_encode_1000_span_trace_to_dict_fast(benchmark):
    from ddtrace.encoding import TraceMsgPackEncoder
    traces = [gen_trace(nspans=1000)]
    encoder = TraceMsgPackEncoder()

    @benchmark
    def fn():
        d = []
        for t in traces:
            d.append([s.to_dict_fast() for s in t])


@pytest.mark.benchmark(group="encoding", min_time=0.005)
def test_encode_1000_span_trace_to_dict_fast2(benchmark):
    from ddtrace.encoding import TraceMsgPackEncoder
    traces = [gen_trace(nspans=1000)]
    encoder = TraceMsgPackEncoder()

    @benchmark
    def fn():
        d = []
        for t in traces:
            d.append([s.to_dict_fast2() for s in t])

'''




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
