# -*- coding: utf-8 -*-
import contextlib
import json
import random
import string
import threading
from unittest import TestCase

from hypothesis import given
from hypothesis import settings
from hypothesis.strategies import dictionaries
from hypothesis.strategies import floats
from hypothesis.strategies import integers
from hypothesis.strategies import text
import mock
import msgpack
import pytest

from ddtrace._trace._limits import MAX_SPAN_META_VALUE_LEN
from ddtrace._trace._span_link import SpanLink
from ddtrace._trace._span_pointer import _SpanPointerDirection
from ddtrace.constants import _ORIGIN_KEY as ORIGIN_KEY
from ddtrace.ext import SpanTypes
from ddtrace.ext.ci import CI_APP_TEST_ORIGIN
from ddtrace.internal._encoding import BufferFull
from ddtrace.internal._encoding import BufferItemTooLarge
from ddtrace.internal._encoding import ListStringTable
from ddtrace.internal._encoding import MsgpackStringTable
from ddtrace.internal.encoding import MSGPACK_ENCODERS
from ddtrace.internal.encoding import JSONEncoder
from ddtrace.internal.encoding import JSONEncoderV2
from ddtrace.internal.encoding import MsgpackEncoderV04
from ddtrace.internal.encoding import MsgpackEncoderV05
from ddtrace.internal.encoding import _EncoderBase
from ddtrace.settings._agent import config as agent_config
from ddtrace.trace import Context
from ddtrace.trace import Span
from tests.utils import DummyTracer


_ORIGIN_KEY = ORIGIN_KEY.encode()


def span_to_tuple(span):
    # type: (Span) -> tuple
    return (
        span.service,
        span.name,
        span.resource,
        span._trace_id_64bits or 0,
        span.span_id or 0,
        span.parent_id or 0,
        span.start_ns or 0,
        span.duration_ns or 0,
        int(bool(span.error)),
        span.get_tags() or {},
        span.get_metrics() or {},
        span.span_type,
    )


def rands(size=6, chars=string.ascii_uppercase + string.digits):
    return "".join(random.choice(chars) for _ in range(size))


def gen_trace(nspans=1000, ntags=50, key_size=15, value_size=20, nmetrics=10):
    root = None
    trace = []
    for i in range(0, nspans):
        parent_id = root.span_id if root else None
        with Span(
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
                span.set_tag(rands(key_size), random.randint(0, 2**16))

            trace.append(span)

            if not root:
                root = span

    return trace


class RefMsgpackEncoder(_EncoderBase):
    content_type = "application/msgpack"

    def normalize(self, span):
        raise NotImplementedError()

    def encode_traces(self, traces):
        normalized_traces = [[self.normalize(span) for span in trace] for trace in traces]
        return self.encode(normalized_traces)[0]

    def encode(self, obj):
        return msgpack.packb(obj), len(obj)

    @staticmethod
    def decode(data):
        return msgpack.unpackb(data, raw=True, strict_map_key=False)


class RefMsgpackEncoderV04(RefMsgpackEncoder):
    def normalize(self, span):
        d = RefMsgpackEncoderV04._span_to_dict(span)
        if not d["error"]:
            del d["error"]
        if not d["parent_id"]:
            del d["parent_id"]
        return d


class RefMsgpackEncoderV05(RefMsgpackEncoder):
    def __init__(self, *args, **kwargs):
        super(RefMsgpackEncoderV05, self).__init__(*args, **kwargs)
        self.string_table = ListStringTable()
        self.string_table.index(ORIGIN_KEY)

    def _index_or_value(self, value):
        if value is None:
            return 0

        if isinstance(value, str):
            return self.string_table.index(value)

        if isinstance(value, dict):
            return {self._index_or_value(k): self._index_or_value(v) for k, v in value.items()}

        return value

    def normalize(self, span):
        return tuple(self._index_or_value(_) for _ in span_to_tuple(span))

    def encode(self, obj):
        try:
            return super(RefMsgpackEncoderV05, self).encode([list(self.string_table), obj])
        finally:
            self.string_table = ListStringTable()
            self.string_table.index(ORIGIN_KEY)


REF_MSGPACK_ENCODERS = {
    "v0.4": RefMsgpackEncoderV04,
    "v0.5": RefMsgpackEncoderV05,
}


class TestEncoders(TestCase):
    """
    Ensures that Encoders serialize the payload as expected.
    """

    def test_encode_traces_json(self):
        # test encoding for JSON format
        traces = [
            [
                Span(name="client.testing", links=[SpanLink(trace_id=12345, span_id=678990)]),
                Span(name="client.testing"),
            ],
            [
                Span(name="client.testing"),
                Span(name="client.testing"),
            ],
            [
                Span(name=b"client.testing"),
                Span(name=b"client.testing"),
            ],
        ]

        encoder = JSONEncoder()
        spans = encoder.encode_traces(traces)
        items = json.loads(spans)

        # test the encoded output that should be a string
        # and the output must be flatten
        assert isinstance(spans, str)
        assert len(items) == 3
        assert len(items[0]) == 2
        assert len(items[0][0]["span_links"]) == 1
        assert len(items[1]) == 2
        assert len(items[2]) == 2
        for i in range(3):
            for j in range(2):
                assert "client.testing" == items[i][j]["name"]

    def test_encode_traces_json_v2(self):
        # test encoding for JSON format
        traces = [
            [
                Span(name="client.testing", span_id=0xAAAAAA, links=[SpanLink(trace_id=12345, span_id=67890)]),
                Span(name="client.testing", span_id=0xAAAAAA),
            ],
            [
                Span(name="client.testing", span_id=0xAAAAAA),
                Span(name="client.testing", span_id=0xAAAAAA),
            ],
            [
                Span(name=b"client.testing", span_id=0xAAAAAA),
                Span(name=b"client.testing", span_id=0xAAAAAA),
            ],
        ]

        encoder = JSONEncoderV2()
        spans = encoder.encode_traces(traces)
        items = json.loads(spans)["traces"]
        # test the encoded output that should be a string
        # and the output must be flatten
        assert isinstance(spans, str)
        assert len(items) == 3
        assert len(items[0]) == 2
        assert len(items[0][0]["span_links"]) == 1
        assert len(items[1]) == 2
        assert len(items[2]) == 2
        for i in range(3):
            for j in range(2):
                assert "client.testing" == items[i][j]["name"]
                assert isinstance(items[i][j]["span_id"], str)
                assert items[i][j]["span_id"] == "0000000000AAAAAA"


def test_encode_meta_struct():
    # test encoding for MsgPack format
    encoder = MSGPACK_ENCODERS["v0.4"](2 << 10, 2 << 10)
    super_span = Span(name="client.testing", trace_id=1)
    payload = {"tttt": {"iuopç": [{"abcd": 1, "bcde": True}, {}]}, "zzzz": b"\x93\x01\x02\x03", "ZZZZ": [1, 2, 3]}

    super_span.set_struct_tag("payload", payload)
    super_span.set_tag("payload", "meta_payload")
    encoder.put(
        [
            super_span,
            Span(name="client.testing", trace_id=1),
        ]
    )

    spans, _ = encoder.encode()
    items = decode(spans)
    assert isinstance(spans, bytes)
    assert len(items) == 1
    assert len(items[0]) == 2
    assert items[0][0][b"trace_id"] == items[0][1][b"trace_id"]
    for j in range(2):
        assert b"client.testing" == items[0][j][b"name"]
    assert msgpack.unpackb(items[0][0][b"meta_struct"][b"payload"]) == payload


def decode(obj, reconstruct=True):
    unpacked = msgpack.unpackb(obj, raw=True, strict_map_key=False)

    if not unpacked or not unpacked[0]:
        return unpacked

    if isinstance(unpacked[0][0], bytes) and reconstruct:
        # v0.5
        table, _traces = unpacked

        def resolve(span):
            return (
                table[span[0]],
                table[span[1]],
                table[span[2]],
                span[3],
                span[4],
                span[5],
                span[6],
                span[7],
                span[8],
                {table[k]: table[v] for k, v in span[9].items()},
                {table[k]: v for k, v in span[10].items()},
                table[span[11]],
            )

        traces = [[resolve(span) for span in trace] for trace in _traces]
    else:
        traces = unpacked

    return traces


def allencodings(f):
    return pytest.mark.parametrize("encoding", MSGPACK_ENCODERS.keys())(f)


def test_msgpack_encoding_after_an_exception_was_raised():
    """Ensure that the encoder's state is consistent after an Exception is raised during encoding"""
    # Encode a trace after a rollback/BufferFull occurs exception
    rolledback_encoder = MsgpackEncoderV05(1 << 12, 1 << 12)
    trace = gen_trace(nspans=1, ntags=100, nmetrics=100, key_size=10, value_size=10)
    rand_string = rands(size=20, chars=string.ascii_letters)
    # trace only has one span
    trace[0].set_tag_str("some_tag", rand_string)
    try:
        # Encode a trace that will trigger a rollback/BufferItemTooLarge exception
        # BufferFull is not raised since only one span is being encoded
        rolledback_encoder.put(trace)
    except BufferItemTooLarge:
        pass
    else:
        pytest.fail("Encoding the trace did not overflow the trace buffer. We should increase the size of the span.")
    # Successfully encode a small trace
    small_trace = gen_trace(nspans=1, ntags=0, nmetrics=0)
    # Add a tag to the small trace that was previously encoded in the encoder's StringTable
    small_trace[0].set_tag_str("previously_encoded_string", rand_string)
    rolledback_encoder.put(small_trace)

    # Encode a trace without triggering a rollback/BufferFull exception
    ref_encoder = MsgpackEncoderV05(1 << 20, 1 << 20)
    ref_encoder.put(small_trace)

    # Ensure the two encoders have the same state
    assert rolledback_encoder.encode() == ref_encoder.encode()


@allencodings
def test_custom_msgpack_encode(encoding):
    encoder = MSGPACK_ENCODERS[encoding](1 << 20, 1 << 20)
    refencoder = REF_MSGPACK_ENCODERS[encoding]()

    trace = gen_trace(nspans=50)

    # Note that we assert on the decoded versions because the encoded
    # can vary due to non-deterministic map key/value positioning
    encoder.put(trace)
    assert decode(refencoder.encode_traces([trace])) == decode(encoder.encode()[0])

    ref_encoded = refencoder.encode_traces([trace, trace])
    encoder.put(trace)
    encoder.put(trace)
    encoded, _ = encoder.encode()
    assert decode(encoded) == decode(ref_encoded)

    # Empty trace (not that this should be done in practice)
    encoder.put([])
    assert decode(refencoder.encode_traces([[]])) == decode(encoder.encode()[0])

    s = Span(None)
    # Need to .finish() to have a duration since the old implementation will not encode
    # duration_ns, the new one will encode as None
    s.finish()
    encoder.put([s])
    assert decode(refencoder.encode_traces([[s]])) == decode(encoder.encode()[0])


def span_type_span():
    s = Span("span_name")
    s.span_type = SpanTypes.WEB
    return s


@allencodings
@pytest.mark.parametrize(
    "span",
    [
        Span("span_name", span_type=SpanTypes.WEB),
        Span("span_name", resource="/my-resource"),
        Span("span_name", service="my-svc"),
        span_type_span(),
    ],
)
def test_msgpack_span_property_variations(encoding, span):
    refencoder = REF_MSGPACK_ENCODERS[encoding]()
    encoder = MSGPACK_ENCODERS[encoding](1 << 10, 1 << 10)

    # Finish the span to ensure a duration exists.
    span.finish()

    trace = [span]
    encoder.put(trace)
    assert decode(refencoder.encode_traces([trace])) == decode(encoder.encode()[0])


class SubString(str):
    pass


class SubInt(int):
    pass


class SubFloat(float):
    pass


@allencodings
@pytest.mark.parametrize(
    "span, tags",
    [
        (Span("name"), {"int": SubInt(123)}),
        (Span("name"), {"float": SubFloat(123.213)}),
        (Span(SubString("name")), {SubString("test"): SubString("test")}),
        (Span("name"), {"unicode": "😐"}),
        (Span("name"), {"😐": "😐"}),
        (
            Span("span_name", service="test-service", resource="test-resource", span_type=SpanTypes.WEB),
            {"metric1": 123, "metric2": "1", "metric3": 12.3, "metric4": "12.0", "tag1": "test", "tag2": "unicode"},
        ),
    ],
)
def test_span_types(encoding, span, tags):
    refencoder = REF_MSGPACK_ENCODERS[encoding]()
    encoder = MSGPACK_ENCODERS[encoding](1 << 10, 1 << 10)

    span.set_tags(tags)

    # Finish the span to ensure a duration exists.
    span.finish()

    trace = [span]
    encoder.put(trace)
    assert decode(refencoder.encode_traces([trace])) == decode(encoder.encode()[0])


def test_span_link_v04_encoding():
    encoder = MSGPACK_ENCODERS["v0.4"](1 << 20, 1 << 20)

    span = Span(
        "s1",
        links=[
            SpanLink(trace_id=1, span_id=2),
            SpanLink(trace_id=3, span_id=4, flags=0),
            SpanLink(
                trace_id=(123 << 64) + 456,
                span_id=6,
                tracestate="congo=t61rcWkgMzE",
                flags=1,
                attributes={
                    "moon": "ears",
                    "link.name": "link_name",
                    "link.kind": "link_kind",
                    "someval": 1,
                    "drop_me": "bye",
                    "key_other": [True, 2, ["hello", 4, {"5"}]],
                },
            ),
        ],
    )
    span._add_span_pointer(
        pointer_kind="some-kind",
        pointer_direction=_SpanPointerDirection.DOWNSTREAM,
        pointer_hash="some-hash",
        extra_attributes={"some": "extra"},
    )
    assert span._links
    # Drop one attribute so SpanLink.dropped_attributes_count is serialized
    [link_6, *others] = [link for link in span._links if link.span_id == 6]
    assert not others
    link_6._drop_attribute("drop_me")
    # Finish the span to ensure a duration exists.
    span.finish()

    encoder.put([span])
    decoded_trace = decode(encoder.encode()[0])
    # ensure one trace was decoded
    assert len(decoded_trace) == 1
    # ensure trace has one span
    assert len(decoded_trace[0]) == 1

    decoded_span = decoded_trace[0][0]
    assert b"span_links" in decoded_span
    assert decoded_span[b"span_links"] == [
        {
            b"trace_id": 1,
            b"span_id": 2,
        },
        {
            b"trace_id": 3,
            b"span_id": 4,
            b"flags": 0 | (1 << 31),
        },
        {
            b"trace_id": 456,
            b"span_id": 6,
            b"attributes": {
                b"moon": b"ears",
                b"link.name": b"link_name",
                b"link.kind": b"link_kind",
                b"someval": b"1",
                b"key_other.0": b"true",
                b"key_other.1": b"2",
                b"key_other.2.0": b"hello",
                b"key_other.2.1": b"4",
                b"key_other.2.2.0": b"5",
            },
            b"dropped_attributes_count": 1,
            b"tracestate": b"congo=t61rcWkgMzE",
            b"flags": 1 | (1 << 31),
            b"trace_id_high": 123,
        },
        {
            b"trace_id": 0,
            b"span_id": 0,
            b"attributes": {
                b"link.kind": b"span-pointer",
                b"ptr.kind": b"some-kind",
                b"ptr.dir": b"d",
                b"ptr.hash": b"some-hash",
                b"some": b"extra",
            },
        },
    ]


@pytest.mark.parametrize(
    "version,trace_native_span_events",
    [
        ("v0.4", False),
        ("v0.4", True),
        ("v0.5", False),
    ],
)
def test_span_event_encoding_msgpack(version, trace_native_span_events):
    expected_top_level_span_encoding = [
        {
            b"name": b"Something went so wrong",
            b"time_unix_nano": 1,
            b"attributes": {b"type": {b"type": 0, b"string_value": b"error"}},
        },
        {
            b"name": b"I can sing!!! acbdefggnmdfsdv k 2e2ev;!|=xxx",
            b"time_unix_nano": 17353464354546,
            b"attributes": {
                b"emotion": {b"type": 0, b"string_value": b"happy"},
                b"rating": {b"type": 3, b"double_value": 9.8},
                b"other": {
                    b"type": 4,
                    b"array_value": {
                        b"values": [
                            {b"type": 2, b"int_value": 1},
                            {b"type": 3, b"double_value": 9.5},
                            {b"type": 2, b"int_value": 1},
                        ]
                    },
                },
                b"idol": {b"type": 1, b"bool_value": False},
            },
        },
        {b"name": b"We are going to the moon", b"time_unix_nano": 2234567890123456},
    ]
    span = Span("s1")
    span._add_event("Something went so wrong", {"type": "error"}, 1)
    span._add_event(
        "I can sing!!! acbdefggnmdfsdv k 2e2ev;!|=xxx",
        {"emotion": "happy", "rating": 9.8, "other": [1, 9.5, 1], "idol": False},
        17353464354546,
    )
    with mock.patch("ddtrace._trace.span.time_ns", return_value=2234567890123456):
        span._add_event("We are going to the moon")

    agent_config.trace_native_span_events = trace_native_span_events

    encoder = MSGPACK_ENCODERS[version](1 << 20, 1 << 20)
    encoder.put([span])
    data = encoder.encode()
    decoded_trace = decode(data[0])
    # ensure one trace was decoded
    assert len(decoded_trace) == 1
    # ensure trace has one span
    assert len(decoded_trace[0]) == 1

    if version == "v0.5":
        encoded_span_meta = decoded_trace[0][0][9]
        assert b"events" in encoded_span_meta
        assert (
            encoded_span_meta[b"events"]
            == b'[{"name": "Something went so wrong", "time_unix_nano": 1, "attributes": {"type": "error"}}, '
            b'{"name": "I can sing!!! acbdefggnmdfsdv k 2e2ev;!|=xxx", "time_unix_nano": 17353464354546, '
            b'"attributes": {"emotion": "happy", "rating": 9.8, "other": [1, 9.5, 1], "idol": false}}, '
            b'{"name": "We are going to the moon", "time_unix_nano": 2234567890123456}]'
        )
    elif trace_native_span_events:
        encoded_span_meta = decoded_trace[0][0]
        assert b"span_events" in encoded_span_meta
        assert encoded_span_meta[b"span_events"] == expected_top_level_span_encoding
    else:
        encoded_span_meta = decoded_trace[0][0][b"meta"]
        assert b"events" in encoded_span_meta
        assert (
            encoded_span_meta[b"events"]
            == b'[{"name": "Something went so wrong", "time_unix_nano": 1, "attributes": {"type": "error"}}, '
            b'{"name": "I can sing!!! acbdefggnmdfsdv k 2e2ev;!|=xxx", "time_unix_nano": 17353464354546, '
            b'"attributes": {"emotion": "happy", "rating": 9.8, "other": [1, 9.5, 1], "idol": false}}, '
            b'{"name": "We are going to the moon", "time_unix_nano": 2234567890123456}]'
        )


def test_span_link_v05_encoding():
    encoder = MSGPACK_ENCODERS["v0.5"](1 << 20, 1 << 20)

    span = Span(
        "s1",
        context=Context(sampling_priority=1),
        links=[
            SpanLink(trace_id=16, span_id=17),
            SpanLink(
                trace_id=(2**127) - 1,
                span_id=(2**64) - 1,
                tracestate="congo=t61rcWkgMzE",
                flags=0,
                attributes={
                    "moon": "ears",
                    "link.name": "link_name",
                    "link.kind": "link_kind",
                    "drop_me": "bye",
                    "key2": ["false", 2, ["hello", 4, {"5"}]],
                },
            ),
        ],
    )
    span._add_span_pointer(
        pointer_kind="some-kind",
        pointer_direction=_SpanPointerDirection.UPSTREAM,
        pointer_hash="some-hash",
    )

    assert len(span._links) == 3
    # Drop one attribute so SpanLink.dropped_attributes_count is serialized
    [link_bignum, *others] = [link for link in span._links if link.span_id == (2**64) - 1]
    assert not others
    link_bignum._drop_attribute("drop_me")

    # Finish the span to ensure a duration exists.
    span.finish()

    encoder.put([span])
    decoded_trace = decode(encoder.encode()[0])
    assert len(decoded_trace) == 1
    assert len(decoded_trace[0]) == 1

    encoded_span_meta = decoded_trace[0][0][9]
    assert b"_dd.span_links" in encoded_span_meta
    assert (
        encoded_span_meta[b"_dd.span_links"] == b"["
        b'{"trace_id": "00000000000000000000000000000010", "span_id": "0000000000000011"}, '
        b'{"trace_id": "7fffffffffffffffffffffffffffffff", "span_id": "ffffffffffffffff", '
        b'"attributes": {"moon": "ears", "link.name": "link_name", "link.kind": "link_kind", '
        b'"key2.0": "false", "key2.1": "2", "key2.2.0": "hello", "key2.2.1": "4", "key2.2.2.0": "5"}, '
        b'"dropped_attributes_count": 1, "tracestate": "congo=t61rcWkgMzE", "flags": 0}, '
        b'{"trace_id": "00000000000000000000000000000000", "span_id": "0000000000000000", '
        b'"attributes": {"ptr.kind": "some-kind", "ptr.dir": "u", "ptr.hash": "some-hash", '
        b'"link.kind": "span-pointer"}}'
        b"]"
    )


@pytest.mark.parametrize(
    "Encoder,item",
    [
        (MsgpackEncoderV04, b"meta"),
        (MsgpackEncoderV05, 9),
    ],
)
def test_encoder_propagates_dd_origin(Encoder, item):
    tracer = DummyTracer()
    encoder = Encoder(1 << 20, 1 << 20)
    with tracer.trace("Root") as root:
        root.context.dd_origin = CI_APP_TEST_ORIGIN
        for _ in range(999):
            with tracer.trace("child"):
                pass

    trace = tracer._span_aggregator.writer.pop()
    assert trace, "DummyWriter failed to encode the trace"

    encoder.put(trace)
    decoded_trace = decode(encoder.encode()[0])
    assert len(decoded_trace) == 1
    assert decoded_trace[0]

    # Ensure encoded trace contains dd_origin tag in all spans
    assert all((_[item][_ORIGIN_KEY] == b"ciapp-test" for _ in decoded_trace[0]))


@allencodings
@given(
    trace_id=integers(min_value=1, max_value=2**128 - 1),
    name=text(),
    service=text(),
    resource=text(),
    meta=dictionaries(text(), text()),
    metrics=dictionaries(text(), floats()),
    error=integers(min_value=-(2**31), max_value=2**31 - 1),
    span_type=text(),
)
@settings(max_examples=200)
def test_custom_msgpack_encode_trace_size(encoding, trace_id, name, service, resource, meta, metrics, error, span_type):
    encoder = MSGPACK_ENCODERS[encoding](1 << 20, 1 << 20)
    span = Span(trace_id=trace_id, name=name, service=service, resource=resource)
    span.set_tags(meta)
    span.set_metrics(metrics)
    span.error = error
    span.span_type = span_type
    trace = [span, span, span]

    encoder.put(trace)

    assert encoder.size == len(encoder.encode()[0])


def test_encoder_buffer_size_limit_v05():
    buffer_size = 1 << 10
    encoder = MsgpackEncoderV05(buffer_size, buffer_size)

    trace = [Span(name="test")]
    encoder.put(trace)
    base_size = encoder.size
    encoder.put(trace)

    trace_size = encoder.size - base_size

    for _ in range(1, int((buffer_size - base_size) / trace_size)):
        encoder.put(trace)

    with pytest.raises(BufferFull):
        encoder.put(trace)

    with pytest.raises(BufferFull):
        encoder.put(trace)


def test_encoder_buffer_item_size_limit_v05():
    max_item_size = 1 << 10
    encoder = MsgpackEncoderV05(max_item_size << 1, max_item_size)

    span = Span(name="test")
    trace = [span]
    encoder.put(trace)
    base_size = encoder.size
    encoder.put(trace)

    trace_size = encoder.size - base_size

    with pytest.raises(BufferItemTooLarge):
        encoder.put([span] * (int(max_item_size / trace_size) + 2))


def test_custom_msgpack_encode_v05():
    encoder = MsgpackEncoderV05(2 << 20, 2 << 20)
    assert encoder.max_size == 2 << 20
    assert encoder.max_item_size == 2 << 20
    trace = [
        Span(name="v05-test", service="foo", resource="GET"),
        Span(name="v05-test", service="foo", resource="POST"),
        Span(name=None, service="bar"),
    ]

    encoder.put(trace)
    assert len(encoder) == 1

    num_bytes = encoder.size
    encoded, num_traces = encoder.flush()
    assert num_traces == 1
    assert num_bytes == len(encoded)
    st, ts = decode(encoded, reconstruct=False)

    def filter_mut(ts):
        return [[[s[i] for i in [0, 1, 2, 5, 7, 8, 9, 10, 11]] for s in t] for t in ts]

    assert st == [b"", _ORIGIN_KEY, b"foo", b"v05-test", b"GET", b"POST", b"bar"]
    assert filter_mut(ts) == [
        [
            [2, 3, 4, 0, 0, 0, {}, {}, 0],
            [2, 3, 5, 0, 0, 0, {}, {}, 0],
            [6, 0, 0, 0, 0, 0, {}, {}, 0],
        ]
    ]


def string_table_test(t, origin_key=False):
    assert len(t) == 1 + origin_key

    assert 0 == t.index("")
    if origin_key:
        assert 1 == t.index(ORIGIN_KEY)

    id1 = t.index("foobar")
    assert len(t) == 2 + origin_key
    assert id1 == t.index("foobar")
    assert len(t) == 2 + origin_key
    id2 = t.index("foobaz")
    assert len(t) == 3 + origin_key
    assert id2 == t.index("foobaz")
    assert len(t) == 3 + origin_key
    assert id1 != id2


def test_msgpack_string_table():
    t = MsgpackStringTable(1 << 10)

    string_table_test(t, origin_key=1)

    size = t.size
    encoded = t.flush()
    assert size == len(encoded)
    assert decode(encoded + b"\xc0", reconstruct=False) == [[b"", _ORIGIN_KEY, b"foobar", b"foobaz"], None]

    assert len(t) == 2
    assert "foobar" not in t


def test_list_string_table():
    t = ListStringTable()

    string_table_test(t)

    assert list(t) == ["", "foobar", "foobaz"]


@contextlib.contextmanager
def _value():
    yield "value"


@pytest.mark.parametrize(
    "data",
    [
        {"trace_id": "trace_id"},
        {"span_id": "span_id"},
        {"parent_id": "parent_id"},
        {"service": True},
        {"resource": 50},
        {"name": [1, 2, 3]},
        {"start_ns": "start_time"},
        {"duration_ns": "duration_time"},
        {"span_type": 100},
        {"_meta": {"num": 100}},
        # Validating behavior with a context manager is a customer regression
        {"_meta": {"key": _value()}},
        {"_metrics": {"key": "value"}},
    ],
)
def test_encoding_invalid_data(data):
    encoder = MsgpackEncoderV04(1 << 20, 1 << 20)

    span = Span(name="test")
    for key, value in data.items():
        setattr(span, key, value)

    trace = [span]
    with pytest.raises(RuntimeError) as e:
        encoder.put(trace)

    assert e.match(r"failed to pack span: <Span\(id="), e
    assert encoder.encode()[0] is None


@allencodings
def test_custom_msgpack_encode_thread_safe(encoding):
    class TracingThread(threading.Thread):
        def __init__(self, encoder, span_count, trace_count):
            super(TracingThread, self).__init__()
            trace = [
                Span(name="span-{}-{}".format(self.name, _), service="threads", resource="TEST")
                for _ in range(span_count)
            ]
            self._encoder = encoder
            self._trace = trace
            self._trace_count = trace_count

        def run(self):
            for _ in range(self._trace_count):
                self._encoder.put(self._trace)

    THREADS = 40
    SPANS = 15
    TRACES = 10
    encoder = MSGPACK_ENCODERS[encoding](2 << 20, 2 << 20)

    ts = [TracingThread(encoder, random.randint(1, SPANS), random.randint(1, TRACES)) for _ in range(THREADS)]
    for t in ts:
        t.start()
    for t in ts:
        t.join()

    unpacked = decode(encoder.encode()[0], reconstruct=True)
    assert unpacked is not None


@pytest.mark.subprocess(parametrize={"encoder_cls": ["JSONEncoder", "JSONEncoderV2"]})
def test_json_encoder_traces_bytes():
    """
    Regression test for: https://github.com/DataDog/dd-trace-py/issues/3115

    Ensure we properly decode `bytes` objects when encoding with the JSONEncoder
    """
    import json
    import os

    import ddtrace.internal.encoding as encoding
    from ddtrace.trace import Span

    encoder_class_name = os.getenv("encoder_cls")

    encoder = getattr(encoding, encoder_class_name)()
    data = encoder.encode_traces(
        [
            [
                Span(name=b"\x80span.a"),
                Span(name="\x80span.b"),
                Span(name="\x80span.b"),
            ]
        ]
    )
    traces = json.loads(data)
    if encoder_class_name == "JSONEncoderV2":
        traces = traces["traces"]

    assert len(traces) == 1
    span_a, span_b, span_c = traces[0]

    assert "\\x80span.a" == span_a["name"]
    assert "\x80span.b" == span_b["name"]
    assert "\x80span.b" == span_c["name"]


def test_encode_large_resource_name_v0_5():
    span = Span(name="client.testing", trace_id=1)
    repetition = 10000
    span.resource = "resource" * repetition

    # test encoding for MsgPack format using the 8388608 default buffer size
    encoder = MSGPACK_ENCODERS["v0.5"](1 << 23, 1 << 23)
    encoder.put(
        [
            span,
        ]
    )
    spans, _ = encoder.encode()
    items = decode(spans)
    original_span_resource = "resource" * repetition
    # size of the pkg length for this one test span - not sure if there's a better way
    pkg_length = 34
    truncated_span_resource = original_span_resource[: MAX_SPAN_META_VALUE_LEN - 14 - pkg_length] + "<truncated>..."
    assert len(items[0][0][2:3][0]) == len(truncated_span_resource)
    assert items[0][0][2:3] == (bytes(truncated_span_resource, "utf-8"),)
    assert isinstance(spans, bytes)
    assert len(items[0]) == 1

    # test encoding for MsgPack format using a very small buffer size for v0.5
    encoder = MSGPACK_ENCODERS["v0.5"](1 << 10, 1 << 10)
    try:
        encoder.put(
            [
                span,
            ]
        )
        spans, _ = encoder.encode()
        items = decode(spans)
    except Exception as err:
        # The encoded span gets truncated
        assert len(items[0][0][2:3][0]) == len(truncated_span_resource)
        assert items[0][0][2:3] == (bytes(truncated_span_resource, "utf-8"),)
        assert isinstance(spans, bytes)
        assert len(items[0]) == 1
        # However: if the buffer has been exceeded we will still drop the payload
        assert "string table is full (current size: 24999, max size: 1024)." in str(err)


def test_encode_large_resource_name_v0_4():
    span = Span(name="client.testing", trace_id=1)
    repetition = 10000
    span.resource = "resource" * repetition

    # test encoding for MsgPack format using the 8388608 default buffer size for v0.5
    encoder = MSGPACK_ENCODERS["v0.4"](1 << 23, 1 << 23)
    encoder.put(
        [
            span,
        ]
    )
    spans, _ = encoder.encode()
    items = decode(spans)
    original_span_resource = "resource" * repetition
    # In v0.4, the resource does not get truncated by the pkg length
    truncated_span_resource = original_span_resource[: MAX_SPAN_META_VALUE_LEN - 14] + "<truncated>..."
    assert len(items[0][0][b"resource"]) == len(truncated_span_resource)
    assert items[0][0][b"resource"] == bytes(truncated_span_resource, "utf-8")
    assert isinstance(spans, bytes)
    assert len(items[0]) == 1

    # test encoding for MsgPack format using a very small buffer size
    encoder = MSGPACK_ENCODERS["v0.4"](1 << 10, 1 << 10)
    try:
        encoder.put(
            [
                span,
            ]
        )
        spans, _ = encoder.encode()
        items = decode(spans)
    except Exception as err:
        # The encoded span gets truncated
        assert len(items[0][0][b"resource"]) == len(truncated_span_resource)
        assert items[0][0][b"resource"] == bytes(truncated_span_resource, "utf-8")
        assert isinstance(spans, bytes)
        assert len(items[0]) == 1
        # However: if the buffer has been exceeded we will still drop the payload
        assert "BufferItemTooLarge" in str(err)
