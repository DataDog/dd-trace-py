import time

from confluent_kafka import TopicPartition
import pytest

from ddtrace.internal.datastreams import data_streams_processor
from ddtrace.internal.datastreams.processor import PROPAGATION_KEY_BASE_64
from ddtrace.internal.datastreams.processor import ConsumerPartitionKey
from ddtrace.internal.datastreams.processor import DataStreamsCtx
from ddtrace.internal.datastreams.processor import PartitionKey
from ddtrace.internal.native import DDSketch
from tests.datastreams.utils import all_pathway_stat_keys
from tests.datastreams.utils import max_commit_offset
from tests.datastreams.utils import max_produce_offset


DSM_TEST_PATH_HEADER_SIZE = 28


class CustomError(Exception):
    pass


@pytest.fixture
def dsm_processor():
    processor = data_streams_processor(reset=True)
    assert processor is not None, "Datastream Monitoring is not enabled"
    yield processor
    # Processor should be recreated by the tracer fixture
    processor.shutdown(timeout=5)


@pytest.mark.parametrize("payload_and_length", [("test", 4), ("你".encode("utf-8"), 3), (b"test2", 5)])
@pytest.mark.parametrize("key_and_length", [("test-key", 8), ("你".encode("utf-8"), 3), (b"t2", 2)])
def test_data_streams_payload_size(dsm_processor, consumer, producer, kafka_topic, payload_and_length, key_and_length):
    payload, payload_length = payload_and_length
    key, key_length = key_and_length
    test_headers = {"1234": "5678"}
    test_header_size = 0
    for k, v in test_headers.items():
        test_header_size += len(k) + len(v)
    expected_payload_size = float(payload_length + key_length)
    expected_payload_size += test_header_size  # to account for headers we add here
    expected_payload_size += len(PROPAGATION_KEY_BASE_64)  # Add in header key length
    expected_payload_size += DSM_TEST_PATH_HEADER_SIZE  # to account for path header we add

    producer.produce(kafka_topic, payload, key=key, headers=test_headers)
    producer.flush()
    consumer.poll()

    # DSM aggregates into 10s wall-clock buckets; produce and consume checkpoints
    # can land in different buckets when the test straddles a boundary. Iterate
    # all buckets and assert each recorded sketch matches expected_payload_size.
    # Hold _lock so the periodic flusher can't mutate _buckets mid-iteration.
    expected_sketch = DDSketch()
    expected_sketch.add(expected_payload_size)
    expected_proto = expected_sketch.to_proto()

    pathway_stats_count = 0
    with dsm_processor._lock:
        for bucket in dsm_processor._buckets.values():
            for stats in bucket.pathway_stats.values():
                pathway_stats_count += 1
                assert stats.payload_size.count >= 1
                assert stats.payload_size.to_proto() == expected_proto
    assert pathway_stats_count >= 1, "no DSM pathway stats recorded for produce/consume"


def test_data_streams_kafka_serializing(dsm_processor, deserializing_consumer, serializing_producer, kafka_topic):
    PAYLOAD = bytes("data streams", encoding="utf-8")
    serializing_producer.produce(kafka_topic, value=PAYLOAD, key="test_key_2")
    serializing_producer.flush()
    message = None
    while message is None or str(message.value()) != str(PAYLOAD):
        message = deserializing_consumer.poll()
    edge_tags = [key[0] for key in all_pathway_stat_keys(dsm_processor)]
    assert any("direction:out" in tags for tags in edge_tags), "Producer DSM checkpoint missing"
    assert any("direction:in" in tags for tags in edge_tags), "Consumer DSM checkpoint missing"


def test_data_streams_kafka(dsm_processor, consumer, producer, kafka_topic, group_id):
    PAYLOAD = bytes("data streams", encoding="utf-8")
    producer.produce(kafka_topic, PAYLOAD, key="test_key_1")
    producer.produce(kafka_topic, PAYLOAD, key="test_key_2")
    producer.flush()
    message = None
    while message is None or str(message.value()) != str(PAYLOAD):
        message = consumer.poll()
    ctx = DataStreamsCtx(dsm_processor, 0, 0, 0)
    parent_hash = ctx._compute_hash(
        sorted(
            ["direction:out", "kafka_cluster_id:5L6g3nShT-eMCtK--X86sw", "type:kafka", "topic:{}".format(kafka_topic)]
        ),
        0,
    )
    child_hash = ctx._compute_hash(
        sorted(
            [
                "direction:in",
                "kafka_cluster_id:5L6g3nShT-eMCtK--X86sw",
                "type:kafka",
                "group:{}".format(group_id),
                "topic:{}".format(kafka_topic),
            ]
        ),
        parent_hash,
    )
    hash_pairs = {(key[1], key[2]) for key in all_pathway_stat_keys(dsm_processor)}
    assert (parent_hash, 0) in hash_pairs
    assert (child_hash, parent_hash) in hash_pairs


def test_data_streams_kafka_offset_monitoring_messages(
    dsm_processor, non_auto_commit_consumer, producer, kafka_topic, group_id
):
    def _read_single_message(consumer):
        message = None
        while message is None or str(message.value()) != str(PAYLOAD):
            message = consumer.poll()
            if message:
                consumer.commit(asynchronous=False, message=message)
                return message

    PAYLOAD = bytes("data streams", encoding="utf-8")
    consumer = non_auto_commit_consumer
    producer.produce(kafka_topic, PAYLOAD, key="test_key_1")
    producer.produce(kafka_topic, PAYLOAD, key="test_key_2")
    producer.flush()

    _message = _read_single_message(consumer)  # noqa: F841

    cluster_id = getattr(producer, "_dd_cluster_id", "") or ""
    produce_key = PartitionKey(kafka_topic, 0, cluster_id)
    commit_key = ConsumerPartitionKey(group_id, kafka_topic, 0, cluster_id)
    assert max_produce_offset(dsm_processor, produce_key) > 0
    first_offset = consumer.committed([TopicPartition(kafka_topic, 0)])[0].offset
    assert first_offset
    assert max_commit_offset(dsm_processor, commit_key) == first_offset

    _message = _read_single_message(consumer)  # noqa: F841
    assert consumer.committed([TopicPartition(kafka_topic, 0)])[0].offset == first_offset + 1
    assert max_commit_offset(dsm_processor, commit_key) == first_offset + 1


def test_data_streams_kafka_offset_monitoring_offsets(
    dsm_processor, non_auto_commit_consumer, producer, kafka_topic, group_id
):
    def _read_single_message(consumer):
        message = None
        while message is None or str(message.value()) != str(PAYLOAD):
            message = consumer.poll()
            if message and message.offset() is not None:
                tp = TopicPartition(message.topic(), message.partition())
                tp.offset = message.offset() + 1
                offsets = [tp]

                consumer.commit(asynchronous=False, offsets=offsets)
                return message

    consumer = non_auto_commit_consumer
    PAYLOAD = bytes("data streams", encoding="utf-8")
    producer.produce(kafka_topic, PAYLOAD, key="test_key_1")
    producer.produce(kafka_topic, PAYLOAD, key="test_key_2")
    producer.flush()

    _message = _read_single_message(consumer)  # noqa: F841

    cluster_id = getattr(producer, "_dd_cluster_id", "") or ""
    produce_key = PartitionKey(kafka_topic, 0, cluster_id)
    commit_key = ConsumerPartitionKey(group_id, kafka_topic, 0, cluster_id)
    assert max_produce_offset(dsm_processor, produce_key) > 0
    first_offset = consumer.committed([TopicPartition(kafka_topic, 0)])[0].offset
    assert first_offset > 0
    assert max_commit_offset(dsm_processor, commit_key) == first_offset

    _message = _read_single_message(consumer)  # noqa: F841
    assert consumer.committed([TopicPartition(kafka_topic, 0)])[0].offset == first_offset + 1
    assert max_commit_offset(dsm_processor, commit_key) == first_offset + 1


def test_data_streams_kafka_offset_monitoring_auto_commit(dsm_processor, consumer, producer, kafka_topic, group_id):
    def _read_single_message(consumer):
        message = None
        while message is None or str(message.value()) != str(PAYLOAD):
            message = consumer.poll(1.0)
            if message:
                return message

    PAYLOAD = bytes("data streams", encoding="utf-8")
    # Only produce one message to avoid race: with two messages, auto-commit can batch both
    # before the test checks, causing offset 2 instead of expected 1.
    producer.produce(kafka_topic, PAYLOAD, key="test_key_1")
    producer.flush()

    # Read a single message, which should commit it automatically since auto commit is true
    _message = _read_single_message(consumer)  # noqa: F841

    cluster_id = getattr(producer, "_dd_cluster_id", "") or ""
    produce_key = PartitionKey(kafka_topic, 0, cluster_id)
    with dsm_processor._lock:
        produce_tracked = any(
            produce_key in bucket.latest_produce_offsets for bucket in dsm_processor._buckets.values()
        )
    assert produce_tracked, "DSM did not track the produce — delivery callback may not have fired"

    def _wait_for_auto_commit_and_fetch_offset(timeout=5.0):
        start_time = time.time()

        while time.time() - start_time < timeout:
            tp = TopicPartition(kafka_topic, 0)
            committed = consumer.committed([tp], timeout=1.0)

            # Check for valid committed offset (> 0, not -1001/_NO_OFFSET)
            if committed and committed[0].offset > 0:
                return committed[0].offset

            time.sleep(0.1)

        return None

    # Auto commit is enabled so we want to wait for the commit event to fire
    first_offset = _wait_for_auto_commit_and_fetch_offset()
    assert first_offset is not None, "Auto-commit did not complete within 5 seconds"
    dsm_offset = max_commit_offset(dsm_processor, ConsumerPartitionKey(group_id, kafka_topic, 0, cluster_id))
    # DSM tracks offsets at poll time while the broker commits asynchronously,
    # so the DSM offset may be >= the broker's reported committed offset.
    assert dsm_offset >= first_offset


def test_data_streams_kafka_produce_api_compatibility(dsm_processor, consumer, producer, empty_kafka_topic):
    kafka_topic = empty_kafka_topic

    PAYLOAD = bytes("data streams", encoding="utf-8")

    # All of these should work
    producer.produce(kafka_topic)
    producer.produce(kafka_topic, PAYLOAD)
    producer.produce(kafka_topic, value=PAYLOAD)
    producer.produce(kafka_topic, PAYLOAD, key="test_key_1")
    producer.produce(kafka_topic, value=PAYLOAD, key="test_key_2")
    producer.produce(kafka_topic, key="test_key_3")
    producer.flush()

    cluster_id = getattr(producer, "_dd_cluster_id", "") or ""
    assert max_produce_offset(dsm_processor, PartitionKey(kafka_topic, 0, cluster_id)) == 5


def test_data_streams_kafka_offset_backlog_has_cluster_id(
    dsm_processor, non_auto_commit_consumer, producer, kafka_topic
):
    """Verify that serialized backlog entries include kafka_cluster_id tag for both produce and commit offsets."""
    PAYLOAD = bytes("cluster id backlog test", encoding="utf-8")
    consumer = non_auto_commit_consumer

    producer.produce(kafka_topic, PAYLOAD, key="test_key_1")
    producer.flush()

    message = None
    while message is None or str(message.value()) != str(PAYLOAD):
        message = consumer.poll()
        if message:
            consumer.commit(asynchronous=False, message=message)

    cluster_id = getattr(producer, "_dd_cluster_id", "") or ""
    if not cluster_id:
        pytest.skip("Test broker does not provide cluster_id")

    serialized = dsm_processor._serialize_buckets()
    assert len(serialized) >= 1
    # Produce and commit backlogs can land in different serialized buckets when the
    # test straddles a 10s wall-clock boundary; flatten across all buckets.
    backlogs = [b for s in serialized for b in s.get("Backlogs", [])]
    commit_backlogs = [b for b in backlogs if "type:kafka_commit" in b["Tags"]]
    produce_backlogs = [b for b in backlogs if "type:kafka_produce" in b["Tags"]]
    assert len(commit_backlogs) >= 1, "Expected at least one kafka_commit backlog entry"
    assert len(produce_backlogs) >= 1, "Expected at least one kafka_produce backlog entry"
    for cb in commit_backlogs:
        assert "kafka_cluster_id:" + cluster_id in cb["Tags"]
    for pb in produce_backlogs:
        assert "kafka_cluster_id:" + cluster_id in pb["Tags"]


def test_data_streams_default_context_propagation(consumer, producer, kafka_topic):
    test_string = "context test"
    PAYLOAD = bytes(test_string, encoding="utf-8")

    producer.produce(kafka_topic, PAYLOAD, key="test_key")
    producer.flush()

    message = None
    while message is None or str(message.value()) != str(PAYLOAD):
        message = consumer.poll()

    # message comes back with expected test string
    assert message.value() == b"context test"

    # DSM header 'dd-pathway-ctx-base64' was propagated in the headers
    assert message.headers()[0][0] == PROPAGATION_KEY_BASE_64
    assert message.headers()[0][1] is not None


def test_span_has_dsm_payload_hash(kafka_tracer, test_spans, consumer, producer, kafka_topic):
    test_string = "payload hash test"
    PAYLOAD = bytes(test_string, encoding="utf-8")

    producer.produce(kafka_topic, PAYLOAD, key="test_payload_hash_key")
    producer.flush()

    message = None
    while message is None or str(message.value()) != str(PAYLOAD):
        message = consumer.poll()

    # message comes back with expected test string
    assert message.value() == b"payload hash test"

    traces = test_spans.pop_traces()
    produce_span = traces[0][0]
    consume_span = traces[len(traces) - 1][0]

    # kafka.produce and kafka.consume span have payload hash
    assert produce_span.name == "kafka.produce"
    assert produce_span.get_tag("pathway.hash") is not None

    assert consume_span.name == "kafka.consume"
    assert consume_span.get_tag("pathway.hash") is not None


@pytest.mark.snapshot(
    ignores=["metrics.kafka.message_offset", "meta.kafka.group_id", "meta.messaging.kafka.bootstrap.servers"]
)
@pytest.mark.subprocess(env={"DD_DATA_STREAMS_ENABLED": "true"}, ddtrace_run=True, err=None)
def test_data_streams_kafka_enabled():
    """Test that verifies DSM is enabled and adds dd-pathway-ctx-base64 header to Kafka messages."""
    import confluent_kafka
    from confluent_kafka import admin as kafka_admin

    from tests.contrib.config import KAFKA_CONFIG

    BOOTSTRAP_SERVERS = "{}:{}".format(KAFKA_CONFIG["host"], KAFKA_CONFIG["port"])
    topic_name = "test_data_streams_kafka_enabled"

    try:
        client = kafka_admin.AdminClient({"bootstrap.servers": BOOTSTRAP_SERVERS})
        list(client.create_topics([kafka_admin.NewTopic(topic_name, 1, 1)]).values())[0].result()
    except Exception:
        pass

    producer = confluent_kafka.Producer({"bootstrap.servers": BOOTSTRAP_SERVERS})
    consumer = confluent_kafka.Consumer(
        {"bootstrap.servers": BOOTSTRAP_SERVERS, "group.id": topic_name, "auto.offset.reset": "earliest"}
    )

    try:
        import time

        consumer.subscribe([topic_name])

        assignment_deadline = time.time() + 5
        while not consumer.assignment() and time.time() < assignment_deadline:
            consumer.poll(timeout=0.1)

        producer.produce(topic_name, b"test")
        producer.flush()

        message = None
        message_deadline = time.time() + 5
        while message is None and time.time() < message_deadline:
            message = consumer.poll(timeout=0.5)

        assert message is not None
        assert "dd-pathway-ctx-base64" in [h[0] for h in message.headers()]

    finally:
        consumer.close()
        producer.flush()
