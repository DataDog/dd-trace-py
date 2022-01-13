# -*- coding: utf-8 -*-
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
import msgpack
import pytest
import six

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
from ddtrace.internal.encoding import MSGPACK_ENCODERS
from ddtrace.internal.encoding import MsgpackEncoderV03
from ddtrace.internal.encoding import MsgpackEncoderV05
from ddtrace.internal.encoding import _EncoderBase
from ddtrace.span import Span
from ddtrace.span import SpanTypes
from ddtrace.tracer import Tracer
from tests.utils import DummyTracer


_ORIGIN_KEY = ORIGIN_KEY.encode()


def span_to_tuple(span):
    # type: (Span) -> tuple
    return (
        span.service,
        span.name,
        span.resource,
        span.trace_id or 0,
        span.span_id or 0,
        span.parent_id or 0,
        span.start_ns or 0,
        span.duration_ns or 0,
        int(bool(span.error)),
        span.meta or {},
        span.metrics or {},
        span.span_type,
    )


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

    def normalize(self, span):
        raise NotImplementedError()

    def encode_traces(self, traces):
        normalized_traces = [[self.normalize(span) for span in trace] for trace in traces]
        return self.encode(normalized_traces)

    def encode(self, obj):
        return msgpack.packb(obj)

    @staticmethod
    def decode(data):
        return msgpack.unpackb(data, raw=True, strict_map_key=False)


class RefMsgpackEncoderV03(RefMsgpackEncoder):
    def normalize(self, span):
        d = span.to_dict()
        if not d["error"]:
            del d["error"]
        return d


class RefMsgpackEncoderV05(RefMsgpackEncoder):
    def __init__(self, *args, **kwargs):
        super(RefMsgpackEncoderV05, self).__init__(*args, **kwargs)
        self.string_table = ListStringTable()
        self.string_table.index(ORIGIN_KEY)

    def _index_or_value(self, value):
        if value is None:
            return 0

        if isinstance(value, six.string_types):
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
    "v0.3": RefMsgpackEncoderV03,
    "v0.4": RefMsgpackEncoderV03,
    "v0.5": RefMsgpackEncoderV05,
}


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

    def test_encode_traces_msgpack_v03(self):
        # test encoding for MsgPack format
        encoder = MsgpackEncoderV03(2 << 10, 2 << 10)
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


@allencodings
def test_custom_msgpack_encode(encoding):
    encoder = MSGPACK_ENCODERS[encoding](1 << 20, 1 << 20)
    refencoder = REF_MSGPACK_ENCODERS[encoding]()

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


@allencodings
@pytest.mark.parametrize(
    "span",
    [
        Span(None, "span_name", span_type=SpanTypes.WEB),
        Span(None, "span_name", resource="/my-resource"),
        Span(None, "span_name", service="my-svc"),
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
    assert decode(refencoder.encode_traces([trace])) == decode(encoder.encode())


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
def test_span_types(encoding, span, tags):
    refencoder = REF_MSGPACK_ENCODERS[encoding]()
    encoder = MSGPACK_ENCODERS[encoding](1 << 10, 1 << 10)

    span.set_tags(tags)

    # Finish the span to ensure a duration exists.
    span.finish()

    trace = [span]
    encoder.put(trace)
    assert decode(refencoder.encode_traces([trace])) == decode(encoder.encode())


@pytest.mark.parametrize(
    "Encoder,item",
    [
        (MsgpackEncoderV03, b"meta"),
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
    trace = tracer.writer.pop()
    encoder.put(trace)
    decoded_trace = decode(encoder.encode())

    # Ensure encoded trace contains dd_origin tag in all spans
    assert all((_[item][_ORIGIN_KEY] == b"ciapp-test" for _ in decoded_trace[0]))


@allencodings
@given(
    name=text(),
    service=text(),
    resource=text(),
    meta=dictionaries(text(), text()),
    metrics=dictionaries(text(), floats()),
    error=integers(min_value=-(2 ** 31), max_value=2 ** 31 - 1),
    span_type=text(),
)
@settings(max_examples=200)
def test_custom_msgpack_encode_trace_size(encoding, name, service, resource, meta, metrics, error, span_type):
    encoder = MSGPACK_ENCODERS[encoding](1 << 20, 1 << 20)
    span = Span(tracer=None, name=name, service=service, resource=resource)
    span.meta = meta
    span.metrics = metrics
    span.error = error
    span.span_type = span_type
    trace = [span, span, span]

    encoder.put(trace)
    assert encoder.size == len(encoder.encode())


def test_encoder_buffer_size_limit_v03():
    buffer_size = 1 << 10
    encoder = MsgpackEncoderV03(buffer_size, buffer_size)

    trace = [Span(tracer=None, name="test")]
    encoder.put(trace)
    trace_size = encoder.size - 1  # This includes the global msgpack array size prefix

    for _ in range(1, int(buffer_size / trace_size)):
        encoder.put(trace)

    with pytest.raises(BufferFull):
        encoder.put(trace)

    with pytest.raises(BufferFull):
        encoder.put(trace)


def test_encoder_buffer_size_limit_v05():
    buffer_size = 1 << 10
    encoder = MsgpackEncoderV05(buffer_size, buffer_size)

    trace = [Span(tracer=None, name="test")]
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


def test_encoder_buffer_item_size_limit_v03():
    max_item_size = 1 << 10
    encoder = MsgpackEncoderV03(max_item_size << 1, max_item_size)

    span = Span(tracer=None, name="test")
    trace = [span]
    encoder.put(trace)
    trace_size = encoder.size - 1  # This includes the global msgpack array size prefix

    with pytest.raises(BufferItemTooLarge):
        encoder.put([span] * (int(max_item_size / trace_size) + 1))


def test_encoder_buffer_item_size_limit_v05():
    max_item_size = 1 << 10
    encoder = MsgpackEncoderV05(max_item_size << 1, max_item_size)

    span = Span(tracer=None, name="test")
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
        Span(tracer=None, name="v05-test", service="foo", resource="GET"),
        Span(tracer=None, name="v05-test", service="foo", resource="POST"),
        Span(tracer=None, name=None, service="bar"),
    ]

    encoder.put(trace)
    assert len(encoder) == 1

    size = encoder.size
    encoded = encoder.flush()
    assert size == len(encoded)
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
    assert decode(encoded + b"\xc0", reconstruct=False) == [[b"", _ORIGIN_KEY, b"foobar", b"foobaz"], None]

    assert len(t) == 2
    assert "foobar" not in t


def test_list_string_table():
    t = ListStringTable()

    string_table_test(t)

    assert list(t) == ["", "foobar", "foobaz"]


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
        {"meta": {"num": 100}},
        {"metrics": {"key": "value"}},
    ],
)
def test_encoding_invalid_data(data):
    encoder = MsgpackEncoderV03(1 << 20, 1 << 20)

    span = Span(tracer=None, name="test")
    for key, value in data.items():
        setattr(span, key, value)

    trace = [span]
    with pytest.raises(TypeError):
        encoder.put(trace)

    assert encoder.encode() is None


@allencodings
def test_custom_msgpack_encode_thread_safe(encoding):
    class TracingThread(threading.Thread):
        def __init__(self, encoder, span_count, trace_count):
            super(TracingThread, self).__init__()
            trace = [
                Span(tracer=None, name="span-{}-{}".format(self.name, _), service="threads", resource="TEST")
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

    unpacked = decode(encoder.encode(), reconstruct=True)
    assert unpacked is not None
