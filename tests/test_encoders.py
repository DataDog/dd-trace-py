import json
from unittest import TestCase

import msgpack

from ddtrace.span import Span
from ddtrace.compat import msgpack_type, string_type
from ddtrace.encoding import JSONEncoder, JSONEncoderV2, MsgpackEncoder, TraceMsgPackEncoder

from .benchmarks.test_encoding import gen_trace


class TestEncoders(TestCase):
    """
    Ensures that Encoders serialize the payload as expected.
    """

    def test_encode_traces_json(self):
        # test encoding for JSON format
        traces = []
        traces.append(
            [Span(name="client.testing", tracer=None), Span(name="client.testing", tracer=None),]
        )
        traces.append(
            [Span(name="client.testing", tracer=None), Span(name="client.testing", tracer=None),]
        )

        encoder = JSONEncoder()
        spans = encoder.encode_traces(traces)
        items = json.loads(spans)

        # test the encoded output that should be a string
        # and the output must be flatten
        assert isinstance(spans, string_type)
        assert len(items) == 2
        assert len(items[0]) == 2
        assert len(items[1]) == 2
        for i in range(2):
            for j in range(2):
                assert "client.testing" == items[i][j]["name"]

    def test_join_encoded_json(self):
        # test encoding for JSON format
        traces = []
        traces.append(
            [Span(name="client.testing", tracer=None), Span(name="client.testing", tracer=None),]
        )
        traces.append(
            [Span(name="client.testing", tracer=None), Span(name="client.testing", tracer=None),]
        )

        encoder = JSONEncoder()

        # Encode each trace on it's own
        encoded_traces = [encoder.encode_trace(trace) for trace in traces]

        # Join the encoded traces together
        data = encoder.join_encoded(encoded_traces)

        # Parse the resulting data
        items = json.loads(data)

        # test the encoded output that should be a string
        # and the output must be flatten
        assert isinstance(data, string_type)
        assert len(items) == 2
        assert len(items[0]) == 2
        assert len(items[1]) == 2
        for i in range(2):
            for j in range(2):
                assert "client.testing" == items[i][j]["name"]

    def test_encode_traces_json_v2(self):
        # test encoding for JSON format
        traces = []
        traces.append(
            [
                Span(name="client.testing", tracer=None, span_id=0xAAAAAA),
                Span(name="client.testing", tracer=None, span_id=0xAAAAAA),
            ]
        )
        traces.append(
            [
                Span(name="client.testing", tracer=None, span_id=0xAAAAAA),
                Span(name="client.testing", tracer=None, span_id=0xAAAAAA),
            ]
        )

        encoder = JSONEncoderV2()
        spans = encoder.encode_traces(traces)
        items = json.loads(spans)["traces"]
        # test the encoded output that should be a string
        # and the output must be flatten
        assert isinstance(spans, string_type)
        assert len(items) == 2
        assert len(items[0]) == 2
        assert len(items[1]) == 2
        for i in range(2):
            for j in range(2):
                assert "client.testing" == items[i][j]["name"]
                assert isinstance(items[i][j]["span_id"], string_type)
                assert items[i][j]["span_id"] == "0000000000AAAAAA"

    def test_join_encoded_json_v2(self):
        # test encoding for JSON format
        traces = []
        traces.append(
            [
                Span(name="client.testing", tracer=None, span_id=0xAAAAAA),
                Span(name="client.testing", tracer=None, span_id=0xAAAAAA),
            ]
        )
        traces.append(
            [
                Span(name="client.testing", tracer=None, span_id=0xAAAAAA),
                Span(name="client.testing", tracer=None, span_id=0xAAAAAA),
            ]
        )

        encoder = JSONEncoderV2()

        # Encode each trace on it's own
        encoded_traces = [encoder.encode_trace(trace) for trace in traces]

        # Join the encoded traces together
        data = encoder.join_encoded(encoded_traces)

        # Parse the resulting data
        items = json.loads(data)["traces"]

        # test the encoded output that should be a string
        # and the output must be flatten
        assert isinstance(data, string_type)
        assert len(items) == 2
        assert len(items[0]) == 2
        assert len(items[1]) == 2
        for i in range(2):
            for j in range(2):
                span = items[i][j]
                assert "client.testing" == span["name"]
                assert isinstance(span["span_id"], string_type)
                assert span["span_id"] == "0000000000AAAAAA"

    def test_encode_traces_msgpack(self):
        # test encoding for MsgPack format
        traces = []
        traces.append(
            [Span(name="client.testing", tracer=None), Span(name="client.testing", tracer=None),]
        )
        traces.append(
            [Span(name="client.testing", tracer=None), Span(name="client.testing", tracer=None),]
        )

        encoder = MsgpackEncoder()
        spans = encoder.encode_traces(traces)
        items = encoder.decode(spans)

        # test the encoded output that should be a string
        # and the output must be flatten
        assert isinstance(spans, msgpack_type)
        assert len(items) == 2
        assert len(items[0]) == 2
        assert len(items[1]) == 2
        for i in range(2):
            for j in range(2):
                assert b"client.testing" == items[i][j][b"name"]

    def test_join_encoded_msgpack(self):
        # test encoding for MsgPack format
        traces = []
        traces.append(
            [Span(name="client.testing", tracer=None), Span(name="client.testing", tracer=None),]
        )
        traces.append(
            [Span(name="client.testing", tracer=None), Span(name="client.testing", tracer=None),]
        )

        encoder = MsgpackEncoder()

        # Encode each individual trace on it's own
        encoded_traces = [encoder.encode_trace(trace) for trace in traces]
        # Join the encoded traces together
        data = encoder.join_encoded(encoded_traces)

        # Parse the encoded data
        items = encoder.decode(data)

        # test the encoded output that should be a string
        # and the output must be flatten
        assert isinstance(data, msgpack_type)
        assert len(items) == 2
        assert len(items[0]) == 2
        assert len(items[1]) == 2
        for i in range(2):
            for j in range(2):
                assert b"client.testing" == items[i][j][b"name"]


def decode(obj):
    return msgpack.unpackb(obj, raw=True)


def test_custom_msgpack_encode():
    tencoder = TraceMsgPackEncoder()
    mencoder = MsgpackEncoder()

    trace = gen_trace(nspans=50)

    # Note that we assert on the decoded versions because the encoded
    # can vary due to non-deterministic map key/value positioning
    assert decode(mencoder.encode_trace(trace)) == decode(tencoder.encode_trace(trace))

    ref_encoded = mencoder.encode_traces([trace, trace])
    encoded = tencoder.encode_traces([trace, trace])
    assert decode(encoded) == decode(ref_encoded)

    # Empty trace (not that this should be done in practice)
    assert decode(mencoder.encode_trace([])) == decode(tencoder.encode_trace([]))

    s = Span(None, None)
    # Need to .finish() to have a duration since the old implementation will not encode
    # duration_ns, the new one will encode as None
    s.finish()
    assert decode(mencoder.encode_trace([s])) == decode(tencoder.encode_trace([s]))


def test_custom_msgpack_join_encoded():
    tencoder = TraceMsgPackEncoder()
    mencoder = MsgpackEncoder()

    trace = gen_trace(nspans=50)

    ref = mencoder.join_encoded([mencoder.encode_trace(trace) for _ in range(10)])
    custom = tencoder.join_encoded([tencoder.encode_trace(trace) for _ in range(10)])
    assert decode(ref) == decode(custom)

    ref = mencoder.join_encoded([mencoder.encode_trace(trace) for _ in range(1)])
    custom = tencoder.join_encoded([tencoder.encode_trace(trace) for _ in range(1)])
    assert decode(ref) == decode(custom)
