import msgpack
from msgpack.fallback import Packer
import pytest

from ddtrace import Tracer
from ddtrace.constants import ORIGIN_KEY
from ddtrace.ext.ci import CI_APP_TEST_ORIGIN
from ddtrace.internal.encoding import MsgpackEncoder
from ddtrace.internal.encoding import _EncoderBase
from tests.tracer.test_encoders import RefMsgpackEncoder
from tests.tracer.test_encoders import gen_trace
from tests.utils import DummyWriter


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


@pytest.mark.benchmark(group="encoding.dd_origin", min_time=0.005)
def test_dd_origin_tagged_via_trace_tags_processor(benchmark):
    """Propagate dd_origin tags to all spans in trace via TraceTagsProcessor"""

    def trace_scenario(tracer):
        """Tracing scenario (includes writing and encoding) for CIApp dd_origin propagation"""
        with tracer.trace("pytest-test") as span:
            span.context.dd_origin = CI_APP_TEST_ORIGIN
            for _ in range(49):
                with tracer.trace(""):
                    pass

    tracer = Tracer()
    dummy_writer = DummyWriter()
    tracer.configure(writer=dummy_writer)
    # TraceTagsProcessor.process_trace() should be called on every span on_span_finish()
    benchmark(trace_scenario, tracer)

    spans = tracer.writer.pop()
    for span in spans:
        assert span.meta[ORIGIN_KEY] == CI_APP_TEST_ORIGIN
