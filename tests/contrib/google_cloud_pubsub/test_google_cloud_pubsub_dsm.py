import threading

import pytest

from ddtrace.internal.datastreams import data_streams_processor
from ddtrace.internal.datastreams.processor import PROPAGATION_KEY_BASE_64
from ddtrace.internal.datastreams.processor import DataStreamsCtx
from ddtrace.internal.native import DDSketch
from tests.datastreams.utils import all_pathway_stat_keys


DSM_TEST_PATH_HEADER_SIZE = 28


@pytest.fixture
def dsm_processor():
    processor = data_streams_processor(reset=True)
    assert processor is not None, "Data Streams Monitoring is not enabled"
    yield processor
    processor.shutdown(timeout=5)


def _wait_for_pathway_directions(processor, *required, timeout=10.0):
    """Poll DSM buckets until checkpoints for each required direction tag appear."""
    import time

    deadline = time.time() + timeout
    while time.time() < deadline:
        tag_strs = [" ".join(key[0]) for key in all_pathway_stat_keys(processor)]
        if all(any(req in tags for tags in tag_strs) for req in required):
            return
        time.sleep(0.1)
    raise AssertionError(f"timed out waiting for DSM checkpoints {required}; saw: {tag_strs}")


def test_dsm_payload_size_produce(dsm_processor, publisher, topic_path):
    """Producer pathway records a payload size that accounts for data, attributes, and the injected pathway header."""
    payload = b"data streams hello"
    test_attrs = {"custom_key": "custom_value"}
    publisher.publish(topic_path, payload, **test_attrs).result(timeout=10)

    _wait_for_pathway_directions(dsm_processor, "direction:out")

    # Verify a non-zero payload-size sketch was recorded on the producer pathway.
    found_produce_sketch = False
    with dsm_processor._lock:
        for bucket in dsm_processor._buckets.values():
            for key, stats in bucket.pathway_stats.items():
                tags = key[0]
                if "direction:out" in tags and any(t.startswith("topic:") for t in tags):
                    assert "type:google-pubsub" in tags
                    assert stats.payload_size.count >= 1
                    found_produce_sketch = True
    assert found_produce_sketch, "expected producer pathway sketch not recorded"


def test_dsm_pathway_linkage(dsm_processor, publisher, topic_path, subscriber, subscription_path):
    """Publishing then subscribing produces linked producer→consumer pathway hashes with the expected tag schema."""
    received = threading.Event()

    def callback(message):
        message.ack()
        received.set()

    future = subscriber.subscribe(subscription_path, callback=callback)
    try:
        publisher.publish(topic_path, b"data streams hello").result(timeout=10)
        assert received.wait(timeout=10), "timed out waiting for subscriber callback"
    finally:
        future.cancel()
        future.result(timeout=5)

    _wait_for_pathway_directions(dsm_processor, "direction:out", "direction:in")

    ctx = DataStreamsCtx(dsm_processor, 0, 0, 0)
    parent_hash = ctx._compute_hash(
        sorted(["direction:out", f"topic:{topic_path}", "type:google-pubsub"]),
        0,
    )
    child_hash = ctx._compute_hash(
        sorted(["direction:in", f"topic:{subscription_path}", "type:google-pubsub"]),
        parent_hash,
    )
    hash_pairs = {(key[1], key[2]) for key in all_pathway_stat_keys(dsm_processor)}
    assert (parent_hash, 0) in hash_pairs, f"producer hash missing; saw {hash_pairs}"
    assert (child_hash, parent_hash) in hash_pairs, f"consumer hash missing; saw {hash_pairs}"


def test_dsm_pathway_header_injected_on_publish(dsm_processor, publisher, topic_path, subscriber, subscription_path):
    """The dd-pathway-ctx-base64 attribute is injected into published messages and survives the round trip."""
    publisher.publish(topic_path, b"data streams hello", custom_key="custom_value").result(timeout=10)

    response = subscriber.pull(subscription=subscription_path, max_messages=1, timeout=10)
    assert len(response.received_messages) == 1
    attributes = dict(response.received_messages[0].message.attributes)
    assert PROPAGATION_KEY_BASE_64 in attributes
    assert attributes[PROPAGATION_KEY_BASE_64]
    # User-provided attributes are preserved alongside the injected pathway key.
    assert attributes["custom_key"] == "custom_value"


def test_dsm_payload_size_matches_expected(dsm_processor, publisher, topic_path):
    """With distributed tracing disabled, payload size = data + attrs + injected pathway key + path header bytes."""
    from tests.utils import override_config

    payload = b"abcdef"  # 6 bytes
    test_attrs = {"k1": "v1"}  # 2 + 2 = 4 bytes of attribute content
    with override_config("google_cloud_pubsub", dict(distributed_tracing_enabled=False)):
        publisher.publish(topic_path, payload, **test_attrs).result(timeout=10)

    _wait_for_pathway_directions(dsm_processor, "direction:out")

    expected_payload_size = float(len(payload) + 4 + len(PROPAGATION_KEY_BASE_64) + DSM_TEST_PATH_HEADER_SIZE)
    expected_sketch = DDSketch()
    expected_sketch.add(expected_payload_size)
    expected_proto = expected_sketch.to_proto()

    with dsm_processor._lock:
        produce_stats = [
            stats
            for bucket in dsm_processor._buckets.values()
            for key, stats in bucket.pathway_stats.items()
            if "direction:out" in key[0]
        ]
    assert len(produce_stats) >= 1
    for stats in produce_stats:
        assert stats.payload_size.count >= 1
        assert stats.payload_size.to_proto() == expected_proto
