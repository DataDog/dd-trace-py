import json
import msgpack

from unittest import TestCase
from nose.tools import eq_, ok_

from ddtrace.span import Span
from ddtrace.compat import msgpack_type, string_type
from ddtrace.encoding import JSONEncoder, MsgpackEncoder


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
        ok_(isinstance(spans, string_type))
        eq_(len(items), 2)
        eq_(len(items[0]), 2)
        eq_(len(items[1]), 2)
        for i in range(2):
            for j in range(2):
                eq_('client.testing', items[i][j]['name'])

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
        ok_(isinstance(data, string_type))
        eq_(len(items), 2)
        eq_(len(items[0]), 2)
        eq_(len(items[1]), 2)
        for i in range(2):
            for j in range(2):
                eq_('client.testing', items[i][j]['name'])

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
        items = msgpack.unpackb(spans)

        # test the encoded output that should be a string
        # and the output must be flatten
        ok_(isinstance(spans, msgpack_type))
        eq_(len(items), 2)
        eq_(len(items[0]), 2)
        eq_(len(items[1]), 2)
        for i in range(2):
            for j in range(2):
                eq_(b'client.testing', items[i][j][b'name'])

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
        items = msgpack.unpackb(data)

        # test the encoded output that should be a string
        # and the output must be flatten
        ok_(isinstance(data, msgpack_type))
        eq_(len(items), 2)
        eq_(len(items[0]), 2)
        eq_(len(items[1]), 2)
        for i in range(2):
            for j in range(2):
                eq_(b'client.testing', items[i][j][b'name'])
