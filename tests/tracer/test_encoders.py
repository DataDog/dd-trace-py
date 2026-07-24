# -*- coding: utf-8 -*-
import json
from unittest import TestCase

import pytest

from ddtrace._trace._span_link import SpanLink
from ddtrace.ext import SpanTypes
from ddtrace.internal._encoding import BufferFull
from ddtrace.internal._encoding import BufferItemTooLarge
from ddtrace.internal.encoding import AgentlessTraceJSONEncoder
from ddtrace.internal.encoding import JSONEncoder
from ddtrace.internal.encoding import JSONEncoderV2
from ddtrace.trace import Span


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

    def test_encode_traces_json_agentless(self):
        span = Span(
            name="span1", trace_id=0x12341234567890ABCDEF, span_id=0x1234567890ABCDEF, service="svc", resource="/r"
        )
        span.set_tag("tag1", "value1")
        span.set_tag("manual.keep")
        span._set_attribute("munir.metric", 1.0)
        span.set_link(trace_id=3, span_id=4)
        span.error = 1
        span.start_ns = 1771941568700091000
        span.duration_ns = 1000000000

        span.span_type = SpanTypes.WEB
        span._set_struct_tag("payload", {"key": "value"})
        encoder = AgentlessTraceJSONEncoder(1 << 11, 1 << 11)
        encoder.put([span])
        encoded_traces = encoder.encode()
        assert encoded_traces, "Expected encoded traces but got empty list"
        [(payload_bytes, n_traces)] = encoded_traces
        data = json.loads(payload_bytes.decode("utf-8"))

        assert n_traces == 1
        assert data == {
            "traces": [
                {
                    "spans": [
                        {
                            "trace_id": "1234567890abcdef",
                            "parent_id": "0000000000000000",
                            "span_id": "1234567890abcdef",
                            "service": "svc",
                            "resource": "/r",
                            "name": "span1",
                            "error": 1,
                            "start": 1771941568700091000,
                            "duration": 1000000000,
                            "meta": {"tag1": "value1", "_dd.compute_stats": "1"},
                            "metrics": {"munir.metric": 1.0, "_trace_root": 1, "_top_level": 1},
                            "type": "web",
                            "span_links": [
                                {"trace_id": "00000000000000000000000000000003", "span_id": "0000000000000004"}
                            ],
                            "meta_struct": {"payload": {"key": "value"}},
                        }
                    ]
                }
            ]
        }


def test_encoder_buffer_size_limit():
    buffer_size = 1 << 10
    encoder = AgentlessTraceJSONEncoder(buffer_size, buffer_size)

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


def test_encoder_buffer_item_size_limit():
    max_item_size = 1 << 10
    encoder = AgentlessTraceJSONEncoder(max_item_size << 1, max_item_size)

    span = Span(name="test")
    encoder.put([span])
    with pytest.raises(BufferItemTooLarge):
        span.set_tag("test", "a" * (max_item_size + 1))
        encoder.put([span])


@pytest.mark.subprocess(parametrize={"encoder_cls": ["JSONEncoder", "JSONEncoderV2"]})
def test_json_encoder_traces_bytes():
    """
    Regression test for: https://github.com/DataDog/dd-trace-py/issues/3115

    Ensure we properly handle `bytes` objects when encoding with the JSONEncoder.

    Note: Invalid UTF-8 bytes are converted to empty string at the Rust layer,
    as we only accept valid str/bytes/None types for span names.
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
                Span(name=b"\x80span.a"),  # Invalid UTF-8 bytes -> empty string
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

    # Invalid UTF-8 bytes are converted to empty string
    assert "" == span_a["name"]
    assert "\x80span.b" == span_b["name"]
    assert "\x80span.b" == span_c["name"]
