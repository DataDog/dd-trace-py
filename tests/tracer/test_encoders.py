# -*- coding: utf-8 -*-
import json
import random
import string
import struct
from unittest import TestCase

import msgpack
import pytest

from ddtrace.compat import msgpack_type
from ddtrace.compat import string_type
from ddtrace.encoding import JSONEncoder
from ddtrace.encoding import JSONEncoderV2
from ddtrace.encoding import MsgpackEncoder
from ddtrace.encoding import _EncoderBase
from ddtrace.span import Span
from ddtrace.span import SpanTypes
from ddtrace.tracer import Tracer


def rands(size=6, chars=string.ascii_uppercase + string.digits):
    return "".join(random.choice(chars) for _ in range(size))


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


class RefMsgpackEncoder(_EncoderBase):
    content_type = "application/msgpack"

    @staticmethod
    def encode(obj):
        return msgpack.packb(obj)

    @staticmethod
    def decode(data):
        return msgpack.unpackb(data, raw=True)

    @staticmethod
    def join_encoded(objs):
        """Join a list of encoded objects together as a msgpack array"""
        buf = b"".join(objs)

        # Prepend array header to buffer
        # https://github.com/msgpack/msgpack-python/blob/f46523b1af7ff2d408da8500ea36a4f9f2abe915/msgpack/fallback.py#L948-L955
        count = len(objs)
        if count <= 0xF:
            return struct.pack("B", 0x90 + count) + buf
        elif count <= 0xFFFF:
            return struct.pack(">BH", 0xDC, count) + buf
        else:
            return struct.pack(">BI", 0xDD, count) + buf


class TestEncoders(TestCase):
    """
    Ensures that Encoders serialize the payload as expected.
    """

    def test_encode_traces_json(self):
        # test encoding for JSON format
        traces = []
        traces.append(
            [
                Span(name="client.testing", tracer=None),
                Span(name="client.testing", tracer=None),
            ]
        )
        traces.append(
            [
                Span(name="client.testing", tracer=None),
                Span(name="client.testing", tracer=None),
            ]
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
            [
                Span(name="client.testing", tracer=None),
                Span(name="client.testing", tracer=None),
            ]
        )
        traces.append(
            [
                Span(name="client.testing", tracer=None),
                Span(name="client.testing", tracer=None),
            ]
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
            [
                Span(name="client.testing", tracer=None),
                Span(name="client.testing", tracer=None),
            ]
        )
        traces.append(
            [
                Span(name="client.testing", tracer=None),
                Span(name="client.testing", tracer=None),
            ]
        )

        encoder = MsgpackEncoder()
        spans = encoder.encode_traces(traces)
        items = encoder._decode(spans)

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
            [
                Span(name="client.testing", tracer=None),
                Span(name="client.testing", tracer=None),
            ]
        )
        traces.append(
            [
                Span(name="client.testing", tracer=None),
                Span(name="client.testing", tracer=None),
            ]
        )

        encoder = MsgpackEncoder()

        # Encode each individual trace on its own
        encoded_traces = [encoder.encode_trace(trace) for trace in traces]
        # Join the encoded traces together
        data = encoder.join_encoded(encoded_traces)

        # Parse the encoded data
        items = encoder._decode(data)

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
    if msgpack.version[:2] < (0, 6):
        return msgpack.unpackb(obj)
    return msgpack.unpackb(obj, raw=True)


def test_custom_msgpack_encode():
    encoder = MsgpackEncoder()
    refencoder = RefMsgpackEncoder()

    trace = gen_trace(nspans=50)

    # Note that we assert on the decoded versions because the encoded
    # can vary due to non-deterministic map key/value positioning
    assert decode(refencoder.encode_trace(trace)) == decode(encoder.encode_trace(trace))

    ref_encoded = refencoder.encode_traces([trace, trace])
    encoded = encoder.encode_traces([trace, trace])
    assert decode(encoded) == decode(ref_encoded)

    # Empty trace (not that this should be done in practice)
    assert decode(refencoder.encode_trace([])) == decode(encoder.encode_trace([]))

    s = Span(None, None)
    # Need to .finish() to have a duration since the old implementation will not encode
    # duration_ns, the new one will encode as None
    s.finish()
    assert decode(refencoder.encode_trace([s])) == decode(encoder.encode_trace([s]))


def test_custom_msgpack_join_encoded():
    encoder = MsgpackEncoder()
    refencoder = RefMsgpackEncoder()

    trace = gen_trace(nspans=50)

    ref = refencoder.join_encoded([refencoder.encode_trace(trace) for _ in range(10)])
    custom = encoder.join_encoded([encoder.encode_trace(trace) for _ in range(10)])
    assert decode(ref) == decode(custom)

    ref = refencoder.join_encoded([refencoder.encode_trace(trace) for _ in range(1)])
    custom = encoder.join_encoded([encoder.encode_trace(trace) for _ in range(1)])
    assert decode(ref) == decode(custom)


def span_type_span():
    s = Span(None, "span_name")
    s.span_type = SpanTypes.WEB
    return s


@pytest.mark.parametrize(
    "span",
    [
        Span(None, "span_name", span_type=SpanTypes.WEB),
        Span(None, "span_name", resource="/my-resource"),
        Span(None, "span_name", service="my-svc"),
        span_type_span(),
    ],
)
def test_msgpack_span_property_variations(span):
    refencoder = RefMsgpackEncoder()
    encoder = MsgpackEncoder()

    # Finish the span to ensure a duration exists.
    span.finish()

    trace = [span]
    assert decode(refencoder.encode_trace(trace)) == decode(encoder.encode_trace(trace))


class SubString(str):
    pass


class SubInt(int):
    pass


class SubFloat(float):
    pass


@pytest.mark.parametrize(
    "span, tags",
    [
        (Span(None, "name"), {"int": SubInt(123)}),
        (Span(None, "name"), {"float": SubFloat(123.213)}),
        (Span(None, SubString("name")), {SubString("test"): SubString("test")}),
        (Span(None, "name"), {"unicode": u"😐"}),
        (Span(None, "name"), {u"😐": u"😐"}),
    ],
)
def test_span_types(span, tags):
    refencoder = RefMsgpackEncoder()
    encoder = MsgpackEncoder()

    span.set_tags(tags)

    # Finish the span to ensure a duration exists.
    span.finish()

    trace = [span]
    assert decode(refencoder.encode_trace(trace)) == decode(encoder.encode_trace(trace))
