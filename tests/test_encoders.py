import json

from unittest import TestCase

from ddtrace.span import Span
from ddtrace.compat import msgpack_type, string_type
from ddtrace.encoding import JSONEncoder, JSONEncoderV2, MsgpackEncoder, TraceMsgPackEncoder


class TestEncoders(TestCase):
    """
    Ensures that Encoders serialize the payload as expected.
    """
    def test_encode_traces_json(self):
        # test encoding for JSON format
        traces = []
        traces.append([
            Span(name='client.testing', tracer=None),
            Span(name='client.testing', tracer=None),
        ])
        traces.append([
            Span(name='client.testing', tracer=None),
            Span(name='client.testing', tracer=None),
        ])

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
                assert 'client.testing' == items[i][j]['name']

    def test_join_encoded_json(self):
        # test encoding for JSON format
        traces = []
        traces.append([
            Span(name='client.testing', tracer=None),
            Span(name='client.testing', tracer=None),
        ])
        traces.append([
            Span(name='client.testing', tracer=None),
            Span(name='client.testing', tracer=None),
        ])

        encoder = JSONEncoder()

        # Encode each trace on it's own
        encoded_traces = [
            encoder.encode_trace(trace)
            for trace in traces
        ]

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
                assert 'client.testing' == items[i][j]['name']

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
        traces.append([
            Span(name='client.testing', tracer=None),
            Span(name='client.testing', tracer=None),
        ])
        traces.append([
            Span(name='client.testing', tracer=None),
            Span(name='client.testing', tracer=None),
        ])

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
                assert b'client.testing' == items[i][j][b'name']

    def test_join_encoded_msgpack(self):
        # test encoding for MsgPack format
        traces = []
        traces.append([
            Span(name='client.testing', tracer=None),
            Span(name='client.testing', tracer=None),
        ])
        traces.append([
            Span(name='client.testing', tracer=None),
            Span(name='client.testing', tracer=None),
        ])

        encoder = MsgpackEncoder()

        # Encode each individual trace on it's own
        encoded_traces = [
            encoder.encode_trace(trace)
            for trace in traces
        ]
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
                assert b'client.testing' == items[i][j][b'name']


def p(s):
    s = "{:,}".format(s).replace(",", " ")
    print(s)


def test_trace_msgpack_encoder():
    from .benchmarks.test_encoding import gen_trace
    tencoder = TraceMsgPackEncoder()
    refencoder = MsgpackEncoder()

    large = gen_trace(nspans=1000, ntags=20, key_size=10, value_size=20, nmetrics=10)
    encoded = refencoder.encode_trace(large)

    from pympler import asizeof

    p(len(refencoder.encode(large[2].to_dict())))
    p(asizeof.asizeof(refencoder.encode(large[2].to_dict())))
    p(asizeof.asizeof(large[2]))
    p(asizeof.asizeof(large))
    p(len(encoded))
    # print(large[0].pprint())
    # p(1024*1024)
    # print(encoded)
    # print(asizeof.asizeof(large) < 1024*1024)
    # print(len(encoded) < 1024*1024)
    assert 0

def test_overflow():
    encoder = MsgpackEncoder()

    encoder.encode("long" * (1024*1024*500000))



def test_tracermsgpack():
    from .benchmarks.test_encoding import gen_trace
    tencoder = TraceMsgPackEncoder()
    refencoder = MsgpackEncoder()

    # large = gen_trace(nspans=1000, ntags=20, key_size=10, value_size=20, nmetrics=10)
    # encoded = refencoder.encode_trace(large)
    # custom_encoded = tencoder.encode_trace(large)
    # data = [[2,3,4]]
    data = [gen_trace(nspans=1, ntags=0, key_size=5, value_size=10, nmetrics=0)]
    ref_encoded = refencoder.encode_traces(data)
    import msgpack
    # ref_encoded = msgpack.packb(data)
    encoded = tencoder.encode_traces(data)

    print(encoded)
    print(len(encoded))

    assert encoded == ref_encoded
    # print(unencoded)

    # assert ref_encoded == encoded
    # print(custom_encoded)
    assert 0

