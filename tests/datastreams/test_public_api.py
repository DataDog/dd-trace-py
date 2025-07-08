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
    with mock.patch("ddtrace.tracer", new=MockedTracer()):
        with mock.patch("ddtrace.config", new=MockedConfig()):
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


def test_manual_checkpoint_behavior():
    headers = {}
    mocked_tracer = MockedTracer()
    with mock.patch("ddtrace.tracer", new=mocked_tracer):
        with mock.patch("ddtrace.config", new=MockedConfig()):
            with mock.patch.object(mocked_tracer.data_streams_processor, "set_checkpoint") as mock_set_checkpoint:
                set_consume_checkpoint("kinesis", "stream-123", headers.get)
                called_tags = mock_set_checkpoint.call_args[0][0]
                assert "manual_checkpoint:true" in called_tags

                mock_set_checkpoint.reset_mock()
                set_consume_checkpoint("kinesis", "stream-123", headers.get, manual_checkpoint=False)
                called_tags = mock_set_checkpoint.call_args[0][0]
                assert "manual_checkpoint:true" not in called_tags


def test_manual_checkpoint_hash_behavior():
    headers = {}
    mocked_tracer = MockedTracer()
    with mock.patch("ddtrace.tracer", new=mocked_tracer):
        with mock.patch("ddtrace.config", new=MockedConfig()):
            got_with_manual = set_consume_checkpoint("kinesis", "stream-123", headers.get)
            got_without_manual = set_consume_checkpoint("kinesis", "stream-123", headers.get, manual_checkpoint=False)

            ctx = DataStreamsCtx(mocked_tracer.data_streams_processor, 0, 0, 0)

            tags_with_manual = ["direction:in", "manual_checkpoint:true", "type:kinesis", "topic:stream-123"]
            expected_with_manual = ctx._compute_hash(sorted(tags_with_manual), 0)

            tags_without_manual = ["direction:in", "type:kinesis", "topic:stream-123"]
            expected_without_manual = ctx._compute_hash(sorted(tags_without_manual), 0)

            assert got_with_manual.hash == expected_with_manual
            assert got_without_manual.hash == expected_without_manual
            assert got_with_manual.hash != got_without_manual.hash
