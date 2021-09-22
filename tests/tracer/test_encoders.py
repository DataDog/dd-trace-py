# -*- coding: utf-8 -*-
import json
import random
import string
from unittest import TestCase

from hypothesis import given
from hypothesis import settings
from hypothesis.strategies import dictionaries
from hypothesis.strategies import floats
from hypothesis.strategies import integers
from hypothesis.strategies import text
import msgpack
import pytest

from ddtrace.constants import ORIGIN_KEY
from ddtrace.ext.ci import CI_APP_TEST_ORIGIN
from ddtrace.internal._encoding import BufferFull
from ddtrace.internal._encoding import BufferItemTooLarge
from ddtrace.internal._encoding import ListStringTable
from ddtrace.internal._encoding import MsgpackStringTable
from ddtrace.internal.compat import msgpack_type
from ddtrace.internal.compat import string_type
from ddtrace.internal.encoding import JSONEncoder
from ddtrace.internal.encoding import JSONEncoderV2
from ddtrace.internal.encoding import MsgpackEncoderV03 as MsgpackEncoder
from ddtrace.internal.encoding import MsgpackEncoderV05
from ddtrace.internal.encoding import _EncoderBase
from ddtrace.span import Span
from ddtrace.span import SpanTypes
from ddtrace.tracer import Tracer
from tests.utils import DummyTracer


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
        return msgpack.unpackb(data, raw=True, strict_map_key=False)


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

    def test_encode_traces_msgpack(self):
        # test encoding for MsgPack format
        encoder = MsgpackEncoder(2 << 10, 2 << 10)
        encoder.put(
            [
                Span(name="client.testing", tracer=None),
                Span(name="client.testing", tracer=None),
            ]
        )
        encoder.put(
            [
                Span(name="client.testing", tracer=None),
                Span(name="client.testing", tracer=None),
            ]
        )

        spans = encoder.encode()
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


def decode(obj):
    if msgpack.version[:2] < (0, 6):
        return msgpack.unpackb(obj)
    return msgpack.unpackb(obj, raw=True, strict_map_key=False)


def test_custom_msgpack_encode():
    encoder = MsgpackEncoder(1 << 20, 1 << 20)
    refencoder = RefMsgpackEncoder()

    trace = gen_trace(nspans=50)

    # Note that we assert on the decoded versions because the encoded
    # can vary due to non-deterministic map key/value positioning
    encoder.put(trace)
    assert decode(refencoder.encode_traces([trace])) == decode(encoder.encode())

    ref_encoded = refencoder.encode_traces([trace, trace])
    encoder.put(trace)
    encoder.put(trace)
    encoded = encoder.encode()
    assert decode(encoded) == decode(ref_encoded)

    # Empty trace (not that this should be done in practice)
    encoder.put([])
    assert decode(refencoder.encode_traces([[]])) == decode(encoder.encode())

    s = Span(None, None)
    # Need to .finish() to have a duration since the old implementation will not encode
    # duration_ns, the new one will encode as None
    s.finish()
    encoder.put([s])
    assert decode(refencoder.encode_traces([[s]])) == decode(encoder.encode())


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
    encoder = MsgpackEncoder(1 << 10, 1 << 10)

    # Finish the span to ensure a duration exists.
    span.finish()

    trace = [span]
    encoder.put(trace)
    assert decode(refencoder.encode_traces([trace])) == decode(encoder.encode())


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
        (
            Span(None, u"span_name", service="test-service", resource="test-resource", span_type=SpanTypes.WEB),
            {"metric1": 123, "metric2": "1", "metric3": 12.3, "metric4": "12.0", "tag1": "test", u"tag2": u"unicode"},
        ),
    ],
)
def test_span_types(span, tags):
    refencoder = RefMsgpackEncoder()
    encoder = MsgpackEncoder(1 << 10, 1 << 10)

    span.set_tags(tags)

    # Finish the span to ensure a duration exists.
    span.finish()

    trace = [span]
    encoder.put(trace)
    assert decode(refencoder.encode_traces([trace])) == decode(encoder.encode())


def test_encoder_propagates_dd_origin():
    tracer = DummyTracer()
    encoder = MsgpackEncoder(1 << 20, 1 << 20)
    with tracer.trace("Root") as root:
        root.context.dd_origin = CI_APP_TEST_ORIGIN
        for _ in range(999):
            with tracer.trace("child"):
                pass
    # Ensure encoded trace contains dd_origin tag in all spans
    trace = tracer.writer.pop()
    encoder.put(trace)
    decoded_trace = decode(encoder.encode())[0]
    for span in decoded_trace:
        assert span[b"meta"][b"_dd.origin"] == b"ciapp-test"


@given(
    name=text(),
    service=text(),
    resource=text(),
    meta=dictionaries(text(), text()),
    metrics=dictionaries(text(), floats()),
    error=integers(),
    span_type=text(),
)
@settings(max_examples=200)
def test_custom_msgpack_encode_trace_size(name, service, resource, meta, metrics, error, span_type):
    encoder = MsgpackEncoder(1 << 20, 1 << 20)
    span = Span(tracer=None, name=name, service=service, resource=resource)
    span.meta = meta
    span.metrics = metrics
    span.error = error
    span.span_type = span_type
    trace = [span, span, span]

    encoder.put(trace)
    assert encoder.size == len(encoder.encode())


def test_encoder_buffer_size_limit():
    buffer_size = 1 << 10
    encoder = MsgpackEncoder(buffer_size, buffer_size)

    trace = [Span(tracer=None, name="test")]
    encoder.put(trace)
    trace_size = encoder.size - 1  # This includes the global msgpack array size prefix

    for _ in range(1, int(buffer_size / trace_size)):
        encoder.put(trace)

    with pytest.raises(BufferFull):
        encoder.put(trace)

    with pytest.raises(BufferFull):
        encoder.put(trace)


def test_encoder_buffer_item_size_limit():
    max_item_size = 1 << 10
    encoder = MsgpackEncoder(max_item_size << 1, max_item_size)

    span = Span(tracer=None, name="test")
    trace = [span]
    encoder.put(trace)
    trace_size = encoder.size - 1  # This includes the global msgpack array size prefix

    with pytest.raises(BufferItemTooLarge):
        encoder.put([span] * (int(max_item_size / trace_size) + 1))


def test_custom_msgpack_encode_v05():
    encoder = MsgpackEncoderV05(2 << 20, 2 << 20)
    assert encoder.max_size == 2 << 20
    assert encoder.max_item_size == 2 << 20
    trace = [
        Span(tracer=None, name="v05-test", service="foo", resource="GET"),
        Span(tracer=None, name="v05-test", service="foo", resource="POST"),
        Span(tracer=None, name=None, service="bar"),
    ]

    encoder.put(trace)
    assert len(encoder) == 1

    size = encoder.size
    encoded = encoder.flush()
    assert size == len(encoded)
    st, ts = decode(encoded)

    def filter_mut(ts):
        return [[[s[i] for i in [0, 1, 2, 5, 7, 8, 9, 10, 11]] for s in t] for t in ts]

    assert st == [b"", ORIGIN_KEY.encode(), b"foo", b"v05-test", b"GET", b"POST", b"bar"]
    assert filter_mut(ts) == [
        [
            [2, 3, 4, 0, 0, 0, {}, {}, 0],
            [2, 3, 5, 0, 0, 0, {}, {}, 0],
            [6, 0, 0, 0, 0, 0, {}, {}, 0],
        ]
    ]


def string_table_test(t, offset=0):
    assert len(t) == 1 + offset
    id1 = t.index("foobar")
    assert len(t) == 2 + offset
    assert id1 == t.index("foobar")
    assert len(t) == 2 + offset
    id2 = t.index("foobaz")
    assert len(t) == 3 + offset
    assert id2 == t.index("foobaz")
    assert len(t) == 3 + offset
    assert id1 != id2


def test_msgpack_string_table():
    t = MsgpackStringTable(1 << 10)

    string_table_test(t, offset=1)

    size = t.size
    encoded = t.flush()
    assert size == len(encoded)
    assert decode(encoded + b"\xc0") == [[b"", ORIGIN_KEY.encode(), b"foobar", b"foobaz"], None]

    assert len(t) == 1
    assert "foobar" not in t


def test_list_string_table():
    t = ListStringTable()

    string_table_test(t)

    assert list(t) == ["", "foobar", "foobaz"]
