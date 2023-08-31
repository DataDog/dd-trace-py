import pytest

from ddtrace import Tracer
from ddtrace.data_streams import set_consume_checkpoint
from ddtrace.data_streams import set_produce_checkpoint
from ddtrace.internal.datastreams.processor import DataStreamsCtx


@pytest.fixture
def tracer():
    t = Tracer()
    t.configure()
    try:
        yield t
    finally:
        t.flush()
        t.shutdown()


def test_public_api(tracer):
    headers = {}
    try:
        del tracer.data_streams_processor._current_context.value
    except AttributeError:
        pass
    set_produce_checkpoint("kinesis", "stream-123", headers.setdefault)
    got = set_consume_checkpoint("kinesis", "stream-123", headers.get)
    ctx = DataStreamsCtx(tracer.data_streams_processor, 0, 0)
    parent_hash = ctx._compute_hash(sorted(["direction:out", "type:kinesis", "topic:stream-123"]), 0)
    expected = ctx._compute_hash(sorted(["direction:in", "type:kinesis", "topic:stream-123"]), parent_hash)
    assert got.hash == expected
