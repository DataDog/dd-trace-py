from ddtrace.internal.datastreams.encoding import decode_var_int_64
from ddtrace.internal.datastreams.encoding import encode_var_int_64
from ddtrace.internal.datastreams.processor import DataStreamsProcessor


def test_encoding():
    n = 1679672748
    expected_encoded = bytearray([216, 150, 238, 193, 12])
    encoded = encode_var_int_64(n)
    assert encoded == expected_encoded
    decoded, b = decode_var_int_64(encoded)
    assert decoded == n
    assert len(b) == 0


def test_pathway_encoding():
    processor = DataStreamsProcessor("")
    ctx = processor.new_pathway()
    ctx.set_checkpoint(["direction:out", "type:kafka", "topic:topic1"])
    expected_pathway_start = ctx.pathway_start_sec
    data = ctx.encode()

    def on_checkpoint_creation(
        hash_value, parent_hash, edge_tags, now_sec, edge_latency_sec, full_pathway_latency_sec, *args, **kwargs
    ):
        assert parent_hash == ctx.hash
        assert sorted(edge_tags) == sorted(["direction:in", "type:kafka", "topic:topic1"])

    processor.on_checkpoint_creation = on_checkpoint_creation
    decoded = processor.decode_pathway(data)
    decoded.set_checkpoint(["direction:in", "type:kafka", "topic:topic1"])
    assert abs(decoded.pathway_start_sec - expected_pathway_start) <= 1e-3


def test_pathway_failed_decoding():
    # testing that a failed decoding leads to a new context being created, instead of an infinite loop
    processor = DataStreamsProcessor("")
    data = b"invalid"  # Invalid data to simulate a decoding failure

    def on_checkpoint_creation(
        hash_value, parent_hash, edge_tags, now_sec, edge_latency_sec, full_pathway_latency_sec, *args, **kwargs
    ):
        if "direction:in" in edge_tags:
            assert parent_hash == 0
            assert sorted(edge_tags) == sorted(["direction:in", "type:kafka", "topic:topic1"])
        else:
            assert sorted(edge_tags) == sorted(["direction:out", "type:kafka", "topic:topic2"])

    processor.on_checkpoint_creation = on_checkpoint_creation

    for i in range(2):
        processor.decode_pathway(data)
        processor.set_checkpoint(["direction:in", "type:kafka", "topic:topic1"])
        processor.set_checkpoint(["direction:out", "type:kafka", "topic:topic2"])
