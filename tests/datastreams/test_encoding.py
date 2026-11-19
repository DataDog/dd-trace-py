import base64
import random
from unittest import mock

from ddtrace.internal.datastreams.encoding import decode_var_int_64
from ddtrace.internal.datastreams.encoding import encode_var_int_64
from ddtrace.internal.datastreams.processor import DataStreamsProcessor
from ddtrace.internal.native import decode_pathway_b64 as native_decode_pathway_b64
from ddtrace.internal.native import encode_pathway_b64 as native_encode_pathway_b64


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


def _reference_encode_b64(ctx):
    # Pure-Python encoding that DataStreamsCtx.encode_b64 must match byte for byte.
    return base64.b64encode(ctx.encode()).decode("utf-8")


def _reference_decode_b64(processor, data):
    # Pure-Python decoding that DataStreamsProcessor.decode_pathway_b64 must match.
    if isinstance(data, str):
        data = data.encode("utf-8")
    return processor.decode_pathway(base64.b64decode(data))


def _decode_outcome(decode, processor, data):
    try:
        with mock.patch("time.time", return_value=1700000000.5):
            ctx = decode(processor, data)
    except Exception as e:
        return type(e)
    return ctx.hash, ctx.pathway_start_sec, ctx.current_edge_start_sec


def _random_timestamp_sec(rng):
    return rng.choice(
        [
            rng.uniform(0, 2e9),
            rng.uniform(-2e9, 0),
            float(rng.randint(-(2**53), 2**53)) / 1e3,
            rng.randint(0, 2**31),
            rng.uniform(-9.3e15, 9.3e15),
        ]
    )


def test_encode_b64_matches_reference_on_random_inputs():
    processor = DataStreamsProcessor("")
    rng = random.Random(830)
    for _ in range(5000):
        ctx = processor.new_pathway()
        ctx.hash = rng.getrandbits(64)
        ctx.pathway_start_sec = _random_timestamp_sec(rng)
        ctx.current_edge_start_sec = _random_timestamp_sec(rng)
        assert ctx.encode_b64() == _reference_encode_b64(ctx)


def test_encode_b64_falls_back_outside_native_range():
    processor = DataStreamsProcessor("")
    ctx = processor.new_pathway()
    ctx.hash = 1
    ctx.pathway_start_sec = 1e17
    ctx.current_edge_start_sec = 1.0
    assert native_encode_pathway_b64(ctx.hash, ctx.pathway_start_sec, ctx.current_edge_start_sec) is None
    assert ctx.encode_b64() == _reference_encode_b64(ctx)


def _random_malformed(rng, encoded):
    choice = rng.randrange(6)
    if choice == 0:
        return encoded[: rng.randrange(len(encoded))]
    if choice == 1:
        return encoded.rstrip("=")
    if choice == 2:
        i = rng.randrange(len(encoded) + 1)
        return encoded[:i] + rng.choice([" ", "\n", "!", "=", "-", "_", "é"]) + encoded[i:]
    if choice == 3:
        i = rng.randrange(len(encoded))
        return (
            encoded[:i]
            + rng.choice("ABCDEFGHIJKLMNOPQRSTUVWXYZabcdefghijklmnopqrstuvwxyz0123456789+/=")
            + encoded[i + 1 :]
        )
    if choice == 4:
        return base64.b64encode(rng.randbytes(rng.randrange(24))).decode("ascii")
    return encoded + rng.choice(["A", "AA==", "QR=="])


def test_decode_pathway_b64_matches_reference_on_random_inputs():
    processor = DataStreamsProcessor("")
    rng = random.Random(830)
    native_hits = 0
    for _ in range(5000):
        ctx = processor.new_pathway()
        ctx.hash = rng.getrandbits(64)
        ctx.pathway_start_sec = _random_timestamp_sec(rng)
        ctx.current_edge_start_sec = _random_timestamp_sec(rng)
        encoded = _reference_encode_b64(ctx)
        native_hits += native_decode_pathway_b64(encoded) is not None
        for data in (encoded, _random_malformed(rng, encoded)):
            for value in (data, data.encode("utf-8")):
                expected = _decode_outcome(_reference_decode_b64, processor, value)
                actual = _decode_outcome(DataStreamsProcessor.decode_pathway_b64, processor, value)
                assert actual == expected, value
    # Most valid encodings must take the native path, not the Python fallback.
    assert native_hits > 4000
