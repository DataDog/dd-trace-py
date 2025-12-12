import time

from confluent_kafka import TopicPartition
import mock
import pytest

from ddtrace._trace.pin import Pin
import ddtrace.internal.datastreams  # noqa: F401 - used as part of mock patching
from ddtrace.internal.datastreams.processor import PROPAGATION_KEY_BASE_64
from ddtrace.internal.datastreams.processor import ConsumerPartitionKey
from ddtrace.internal.datastreams.processor import DataStreamsCtx
from ddtrace.internal.datastreams.processor import PartitionKey
from ddtrace.internal.native import DDSketch
from tests.datastreams.test_public_api import MockedTracer


DSM_TEST_PATH_HEADER_SIZE = 28


class CustomError(Exception):
    pass


@pytest.fixture
def dsm_processor(tracer):
    processor = tracer.data_streams_processor
    with mock.patch("ddtrace.internal.datastreams.data_streams_processor", return_value=processor):
        yield processor
        # flush buckets for the next test run
        processor.periodic()


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

    try:
        del dsm_processor._current_context.value
    except AttributeError:
        pass

    producer.produce(kafka_topic, payload, key=key, headers=test_headers)
    producer.flush()
    consumer.poll()
    buckets = dsm_processor._buckets
    assert len(buckets) == 1
    first = list(buckets.values())[0].pathway_stats
    for _bucket_name, bucket in first.items():
        assert bucket.payload_size.count >= 1

        expected_sketch = DDSketch()
        expected_sketch.add(expected_payload_size)
        assert bucket.payload_size.to_proto() == expected_sketch.to_proto()


def test_data_streams_kafka_serializing(dsm_processor, deserializing_consumer, serializing_producer, kafka_topic):
    PAYLOAD = bytes("data streams", encoding="utf-8")
    try:
        del dsm_processor._current_context.value
    except AttributeError:
        pass
    serializing_producer.produce(kafka_topic, value=PAYLOAD, key="test_key_2")
    serializing_producer.flush()
    message = None
    while message is None or str(message.value()) != str(PAYLOAD):
        message = deserializing_consumer.poll()
    buckets = dsm_processor._buckets
    assert len(buckets) == 1


def test_data_streams_kafka(dsm_processor, consumer, producer, kafka_topic):
    PAYLOAD = bytes("data streams", encoding="utf-8")
    try:
        del dsm_processor._current_context.value
    except AttributeError:
        pass
    producer.produce(kafka_topic, PAYLOAD, key="test_key_1")
    producer.produce(kafka_topic, PAYLOAD, key="test_key_2")
    producer.flush()
    message = None
    while message is None or str(message.value()) != str(PAYLOAD):
        message = consumer.poll()
    buckets = dsm_processor._buckets
    assert len(buckets) == 1
    first = list(buckets.values())[0].pathway_stats
    ctx = DataStreamsCtx(MockedTracer().data_streams_processor, 0, 0, 0)
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
                "group:test_group",
                "topic:{}".format(kafka_topic),
            ]
        ),
        parent_hash,
    )
    assert (parent_hash, 0) in [(tag[1], tag[2]) for tag in first.keys()]
    assert (child_hash, parent_hash) in [(tag[1], tag[2]) for tag in first.keys()]


def test_data_streams_kafka_offset_monitoring_messages(dsm_processor, non_auto_commit_consumer, producer, kafka_topic):
    def _read_single_message(consumer):
        message = None
        while message is None or str(message.value()) != str(PAYLOAD):
            message = consumer.poll()
            if message:
                consumer.commit(asynchronous=False, message=message)
                return message

    PAYLOAD = bytes("data streams", encoding="utf-8")
    consumer = non_auto_commit_consumer
    try:
        del dsm_processor._current_context.value
    except AttributeError:
        pass
    buckets = dsm_processor._buckets
    producer.produce(kafka_topic, PAYLOAD, key="test_key_1")
    producer.produce(kafka_topic, PAYLOAD, key="test_key_2")
    producer.flush()

    _message = _read_single_message(consumer)  # noqa: F841

    assert len(buckets) == 1
    assert list(buckets.values())[0].latest_produce_offsets[PartitionKey(kafka_topic, 0)] > 0
    first_offset = consumer.committed([TopicPartition(kafka_topic, 0)])[0].offset
    assert first_offset
    assert (
        list(buckets.values())[0].latest_commit_offsets[ConsumerPartitionKey("test_group", kafka_topic, 0)]
        == first_offset
    )

    _message = _read_single_message(consumer)  # noqa: F841
    assert consumer.committed([TopicPartition(kafka_topic, 0)])[0].offset == first_offset + 1
    assert (
        list(buckets.values())[0].latest_commit_offsets[ConsumerPartitionKey("test_group", kafka_topic, 0)]
        == first_offset + 1
    )


def test_data_streams_kafka_offset_monitoring_offsets(dsm_processor, non_auto_commit_consumer, producer, kafka_topic):
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
    try:
        del dsm_processor._current_context.value
    except AttributeError:
        pass
    producer.produce(kafka_topic, PAYLOAD, key="test_key_1")
    producer.produce(kafka_topic, PAYLOAD, key="test_key_2")
    producer.flush()

    _message = _read_single_message(consumer)  # noqa: F841

    buckets = dsm_processor._buckets
    assert len(buckets) == 1
    assert list(buckets.values())[0].latest_produce_offsets[PartitionKey(kafka_topic, 0)] > 0
    first_offset = consumer.committed([TopicPartition(kafka_topic, 0)])[0].offset
    assert first_offset > 0
    assert (
        list(buckets.values())[0].latest_commit_offsets[ConsumerPartitionKey("test_group", kafka_topic, 0)]
        == first_offset
    )

    _message = _read_single_message(consumer)  # noqa: F841
    assert consumer.committed([TopicPartition(kafka_topic, 0)])[0].offset == first_offset + 1
    assert (
        list(buckets.values())[0].latest_commit_offsets[ConsumerPartitionKey("test_group", kafka_topic, 0)]
        == first_offset + 1
    )


def test_data_streams_kafka_offset_monitoring_auto_commit(dsm_processor, consumer, producer, kafka_topic):
    def _read_single_message(consumer):
        message = None
        while message is None or str(message.value()) != str(PAYLOAD):
            message = consumer.poll(1.0)
            if message:
                return message

    PAYLOAD = bytes("data streams", encoding="utf-8")
    try:
        del dsm_processor._current_context.value
    except AttributeError:
        pass
    producer.produce(kafka_topic, PAYLOAD, key="test_key_1")
    producer.produce(kafka_topic, PAYLOAD, key="test_key_2")
    producer.flush()

    buckets = dsm_processor._buckets
    # Read a single message, which should commit it automatically since auto commit is true
    _message = _read_single_message(consumer)  # noqa: F841

    assert len(buckets) == 1
    assert list(buckets.values())[0].latest_produce_offsets[PartitionKey(kafka_topic, 0)] > 0

    # Auto commit is enabled so we want to wait for the commit event to fire
    time.sleep(1)
    first_offset = consumer.committed([TopicPartition(kafka_topic, 0)])[0].offset
    if first_offset:
        assert (
            list(buckets.values())[0].latest_commit_offsets[ConsumerPartitionKey("test_group", kafka_topic, 0)]
            == first_offset
        )


def test_data_streams_kafka_produce_api_compatibility(dsm_processor, consumer, producer, empty_kafka_topic):
    kafka_topic = empty_kafka_topic

    PAYLOAD = bytes("data streams", encoding="utf-8")
    try:
        del dsm_processor._current_context.value
    except AttributeError:
        pass

    # All of these should work
    producer.produce(kafka_topic)
    producer.produce(kafka_topic, PAYLOAD)
    producer.produce(kafka_topic, value=PAYLOAD)
    producer.produce(kafka_topic, PAYLOAD, key="test_key_1")
    producer.produce(kafka_topic, value=PAYLOAD, key="test_key_2")
    producer.produce(kafka_topic, key="test_key_3")
    producer.flush()

    buckets = dsm_processor._buckets
    assert len(buckets) == 1
    assert list(buckets.values())[0].latest_produce_offsets[PartitionKey(kafka_topic, 0)] == 5


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


def test_span_has_dsm_payload_hash(dummy_tracer, consumer, producer, kafka_topic):
    Pin._override(producer, tracer=dummy_tracer)
    Pin._override(consumer, tracer=dummy_tracer)

    test_string = "payload hash test"
    PAYLOAD = bytes(test_string, encoding="utf-8")

    producer.produce(kafka_topic, PAYLOAD, key="test_payload_hash_key")
    producer.flush()

    message = None
    while message is None or str(message.value()) != str(PAYLOAD):
        message = consumer.poll()

    # message comes back with expected test string
    assert message.value() == b"payload hash test"

    traces = dummy_tracer.pop_traces()
    produce_span = traces[0][0]
    consume_span = traces[len(traces) - 1][0]

    # kafka.produce and kafka.consume span have payload hash
    assert produce_span.name == "kafka.produce"
    assert produce_span.get_tag("pathway.hash") is not None

    assert consume_span.name == "kafka.consume"
    assert consume_span.get_tag("pathway.hash") is not None

    Pin._override(consumer, tracer=None)
    Pin._override(producer, tracer=None)
