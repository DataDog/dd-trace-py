from kafka import TopicPartition
from kafka.structs import OffsetAndMetadata
import pytest

from ddtrace.contrib.internal.kafka_python.patch import patch
from ddtrace.contrib.internal.kafka_python.patch import unpatch
from ddtrace.internal.datastreams import data_streams_processor
from ddtrace.internal.datastreams.processor import PROPAGATION_KEY_BASE_64
from ddtrace.internal.datastreams.processor import ConsumerPartitionKey
from ddtrace.internal.datastreams.processor import DataStreamsCtx
from ddtrace.internal.datastreams.processor import PartitionKey
from tests.datastreams.utils import max_commit_offset
from tests.datastreams.utils import max_produce_offset
from tests.datastreams.utils import pathway_stats_merged

from .utils import GROUP_ID
from .utils import KEY
from .utils import PAYLOAD
from .utils import create_topic
from .utils import iterate_records
from .utils import make_consumer
from .utils import make_producer
from .utils import poll_records


@pytest.fixture(autouse=True)
def patch_kafka_python():
    patch()
    yield
    unpatch()


@pytest.fixture
def dsm_processor():
    processor = data_streams_processor(reset=True)
    assert processor is not None, "Datastream Monitoring is not enabled"
    yield processor
    processor.shutdown(timeout=5)


def _offset_and_metadata(offset):
    if len(OffsetAndMetadata._fields) == 3:
        return OffsetAndMetadata(offset, "", -1)
    return OffsetAndMetadata(offset, "")


def _cluster_id(client_cluster):
    return getattr(client_cluster, "cluster_id", None) or ""


def _produce(topic, count=1):
    producer = make_producer()
    try:
        metadata = [producer.send(topic, value=PAYLOAD, key=KEY).get(timeout=30) for _ in range(count)]
        return metadata, _cluster_id(producer._metadata)
    finally:
        producer.close()


def _edge_tags(direction, topic, cluster_id, group=None):
    tags = [f"direction:{direction}", f"topic:{topic}", "type:kafka"]
    if group:
        tags.append(f"group:{group}")
    if cluster_id:
        tags.append(f"kafka_cluster_id:{cluster_id}")
    return sorted(tags)


@pytest.mark.parametrize("read", [iterate_records, poll_records])
def test_pathway_links_consumer_to_producer(dsm_processor, read):
    topic = create_topic("pathway")
    _, producer_cluster_id = _produce(topic)

    consumer = make_consumer(topic)
    try:
        (record,) = read(consumer, 1)
        consumer_cluster_id = _cluster_id(consumer._client.cluster)
    finally:
        consumer.close(autocommit=False)

    assert any(key == PROPAGATION_KEY_BASE_64 for key, _ in record.headers)

    producer_tags = _edge_tags("out", topic, producer_cluster_id)
    consumer_tags = _edge_tags("in", topic, consumer_cluster_id, GROUP_ID)
    ctx = DataStreamsCtx(dsm_processor, 0, 0, 0)
    producer_hash = ctx._compute_hash(producer_tags, 0)
    consumer_hash = ctx._compute_hash(consumer_tags, producer_hash)

    stats = pathway_stats_merged(dsm_processor)
    assert stats[(",".join(producer_tags), producer_hash, 0)].edge_latency.count == 1
    assert stats[(",".join(consumer_tags), consumer_hash, producer_hash)].edge_latency.count == 1


def test_user_headers_are_kept(dsm_processor):
    topic = create_topic("user_headers")
    producer = make_producer()
    try:
        producer.send(topic, value=PAYLOAD, headers=[("custom", b"value")]).get(timeout=30)
    finally:
        producer.close()

    consumer = make_consumer(topic)
    try:
        (record,) = iterate_records(consumer, 1)
    finally:
        consumer.close(autocommit=False)

    header_keys = [key for key, _ in record.headers]
    assert header_keys[0] == "custom"
    assert PROPAGATION_KEY_BASE_64 in header_keys


def test_produce_offsets_tracked(dsm_processor):
    topic = create_topic("produce_offsets")
    metadata, cluster_id = _produce(topic, count=2)

    assert max_produce_offset(dsm_processor, PartitionKey(topic, 0, cluster_id)) == metadata[-1].offset


@pytest.mark.parametrize("explicit_offsets", [False, True])
def test_manual_commit_offsets_tracked(dsm_processor, explicit_offsets):
    topic = create_topic("manual_commit")
    _produce(topic, count=2)

    consumer = make_consumer(topic)
    try:
        records = iterate_records(consumer, 2)
        cluster_id = _cluster_id(consumer._client.cluster)
        if explicit_offsets:
            consumer.commit({TopicPartition(topic, 0): _offset_and_metadata(records[-1].offset + 1)})
        else:
            consumer.commit()
    finally:
        consumer.close(autocommit=False)

    key = ConsumerPartitionKey(GROUP_ID, topic, 0, cluster_id)
    assert max_commit_offset(dsm_processor, key) == records[-1].offset + 1


def test_auto_commit_offsets_tracked(dsm_processor):
    topic = create_topic("auto_commit")
    _produce(topic, count=2)

    consumer = make_consumer(topic, enable_auto_commit=True)
    try:
        records = iterate_records(consumer, 2)
        cluster_id = _cluster_id(consumer._client.cluster)
    finally:
        consumer.close()

    key = ConsumerPartitionKey(GROUP_ID, topic, 0, cluster_id)
    assert max_commit_offset(dsm_processor, key) == records[-1].offset + 1
