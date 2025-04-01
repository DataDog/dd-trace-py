import mock

from ddtrace.data_streams import set_consume_checkpoint
from ddtrace.data_streams import set_produce_checkpoint
from ddtrace.internal.datastreams.processor import DataStreamsCtx
from ddtrace.internal.datastreams.processor import DataStreamsProcessor


class MockedTracer:
    def __init__(self):
        self.data_streams_processor = DataStreamsProcessor("http://localhost:8126")


class MockedConfig:
    def __init__(self):
        self._data_streams_enabled = True


def test_public_api():
    headers = {}
    with mock.patch("ddtrace.internal.datastreams.processor.ddtrace.tracer", new=MockedTracer()):
        with mock.patch("ddtrace.internal.datastreams.processor.ddtrace.config", new=MockedConfig()):
            set_produce_checkpoint("kinesis", "stream-123", headers.setdefault)
            got = set_consume_checkpoint("kinesis", "stream-123", headers.get)
            ctx = DataStreamsCtx(MockedTracer().data_streams_processor, 0, 0, 0)
            parent_hash = ctx._compute_hash(
                sorted(["direction:out", "manual_checkpoint:true", "type:kinesis", "topic:stream-123"]), 0
            )
            expected = ctx._compute_hash(
                sorted(["direction:in", "manual_checkpoint:true", "type:kinesis", "topic:stream-123"]), parent_hash
            )
            assert got.hash == expected
