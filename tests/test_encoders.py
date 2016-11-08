from unittest import TestCase

from nose.tools import eq_, ok_

from ddtrace.span import Span
from ddtrace.compat import string_type, json
from ddtrace.encoding import encode_traces


class TestEncoders(TestCase):
    """
    Ensures that Encoders serialize the payload as expected.
    """
    def test_encode_traces(self):
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

        spans = encode_traces(traces)
        items = json.loads(spans)

        # test the encoded output that should be a string
        # and the output must be flatten
        ok_(isinstance(spans, string_type))
        eq_(len(items), 4)
