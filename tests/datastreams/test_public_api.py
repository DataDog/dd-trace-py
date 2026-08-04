import pytest


def _ignore_dsm_flush_err(stderr):
    for line in stderr.splitlines():
        if not line.strip():
            continue
        if "failed to send data stream stats payload" in line:
            continue
        return False
    return True


@pytest.mark.subprocess(env={"DD_DATA_STREAMS_ENABLED": "true"}, err=_ignore_dsm_flush_err)
def test_public_api():
    from ddtrace.data_streams import set_consume_checkpoint
    from ddtrace.data_streams import set_produce_checkpoint
    from ddtrace.internal.datastreams import data_streams_processor
    from ddtrace.internal.datastreams.processor import DataStreamsCtx

    headers = {}

    set_produce_checkpoint("kinesis", "stream-123", headers.setdefault)
    got = set_consume_checkpoint("kinesis", "stream-123", headers.get)
    processors = data_streams_processor()
    assert processors is not None, "Datastream Monitoring is not enabled"
    ctx = DataStreamsCtx(processors, 0, 0, 0)
    parent_hash = ctx._compute_hash(
        sorted(["direction:out", "manual_checkpoint:true", "type:kinesis", "topic:stream-123"]), 0
    )
    expected = ctx._compute_hash(
        sorted(["direction:in", "manual_checkpoint:true", "type:kinesis", "topic:stream-123"]), parent_hash
    )
    assert got.hash == expected


@pytest.mark.subprocess(env={"DD_DATA_STREAMS_ENABLED": "true"})
def test_manual_checkpoint_behavior():
    import mock

    from ddtrace.data_streams import set_consume_checkpoint
    from ddtrace.internal.datastreams import data_streams_processor

    headers = {}
    processor = data_streams_processor()
    with mock.patch.object(processor, "set_checkpoint") as mock_set_checkpoint:
        set_consume_checkpoint("kinesis", "stream-123", headers.get)
        called_tags = mock_set_checkpoint.call_args[0][0]
        assert "manual_checkpoint:true" in called_tags

        mock_set_checkpoint.reset_mock()
        set_consume_checkpoint("kinesis", "stream-123", headers.get, manual_checkpoint=False)
        called_tags = mock_set_checkpoint.call_args[0][0]
        assert "manual_checkpoint:true" not in called_tags


@pytest.mark.subprocess(env={"DD_DATA_STREAMS_ENABLED": "true"})
def test_additional_tags_behavior():
    import mock

    from ddtrace.data_streams import set_consume_checkpoint
    from ddtrace.data_streams import set_produce_checkpoint
    from ddtrace.internal.datastreams import data_streams_processor

    processor = data_streams_processor()

    # set_checkpoint is mocked (no wraps) so no real checkpoint is created and no stats are
    # flushed on shutdown; we only assert the tags forwarded to the processor. Fresh empty
    # carriers keep decode_pathway_b64/encode_b64 off the mocked return value.
    with mock.patch.object(processor, "set_checkpoint") as mock_set_checkpoint:
        set_produce_checkpoint("eventbridge", "my-detail", {}.setdefault, tags=["exchange:my-bus"])
        produce_tags = mock_set_checkpoint.call_args[0][0]
        assert "type:eventbridge" in produce_tags
        assert "topic:my-detail" in produce_tags
        assert "direction:out" in produce_tags
        assert "manual_checkpoint:true" in produce_tags
        assert "exchange:my-bus" in produce_tags

        mock_set_checkpoint.reset_mock()
        set_consume_checkpoint("eventbridge", "my-detail", {}.get, tags=["exchange:my-bus"])
        consume_tags = mock_set_checkpoint.call_args[0][0]
        assert "exchange:my-bus" in consume_tags
        assert "direction:in" in consume_tags


@pytest.mark.subprocess(env={"DD_DATA_STREAMS_ENABLED": "true"}, err=_ignore_dsm_flush_err)
def test_additional_tags_hash_behavior():
    from ddtrace.data_streams import set_consume_checkpoint
    from ddtrace.internal.datastreams import data_streams_processor
    from ddtrace.internal.datastreams.processor import DataStreamsCtx

    headers = {}

    got_default = set_consume_checkpoint("eventbridge", "my-detail", headers.get)
    got_with_tags = set_consume_checkpoint("eventbridge", "my-detail", headers.get, tags=["exchange:my-bus"])

    processor = data_streams_processor()
    assert processor is not None, "Datastream Monitoring is not enabled"
    ctx = DataStreamsCtx(processor, 0, 0, 0)

    expected_with_tags = ctx._compute_hash(
        sorted(["direction:in", "manual_checkpoint:true", "type:eventbridge", "topic:my-detail", "exchange:my-bus"]),
        0,
    )

    assert got_with_tags.hash == expected_with_tags
    # Additional tags must change the pathway hash relative to the default tag set.
    assert got_with_tags.hash != got_default.hash


@pytest.mark.subprocess(env={"DD_DATA_STREAMS_ENABLED": "true"}, err=_ignore_dsm_flush_err)
def test_manual_checkpoint_hash_behavior():
    from ddtrace.data_streams import set_consume_checkpoint
    from ddtrace.internal.datastreams import data_streams_processor
    from ddtrace.internal.datastreams.processor import DataStreamsCtx

    headers = {}

    got_with_manual = set_consume_checkpoint("kinesis", "stream-123", headers.get)
    got_without_manual = set_consume_checkpoint("kinesis", "stream-123", headers.get, manual_checkpoint=False)

    processor = data_streams_processor()
    assert processor is not None, "Datastream Monitoring is not enabled"
    ctx = DataStreamsCtx(processor, 0, 0, 0)

    tags_with_manual = ["direction:in", "manual_checkpoint:true", "type:kinesis", "topic:stream-123"]
    expected_with_manual = ctx._compute_hash(sorted(tags_with_manual), 0)

    tags_without_manual = ["direction:in", "type:kinesis", "topic:stream-123"]
    expected_without_manual = ctx._compute_hash(sorted(tags_without_manual), 0)

    assert got_with_manual.hash == expected_with_manual
    assert got_without_manual.hash == expected_without_manual
    assert got_with_manual.hash != got_without_manual.hash
