from aiokafka.structs import TopicPartition
import pytest

from ddtrace.contrib.internal.aiokafka.patch import patch
from ddtrace.contrib.internal.aiokafka.patch import unpatch
from ddtrace.internal.datastreams import data_streams_processor
from ddtrace.internal.datastreams.processor import PROPAGATION_KEY_BASE_64
from ddtrace.internal.datastreams.processor import ConsumerPartitionKey
from ddtrace.internal.datastreams.processor import DataStreamsCtx
from ddtrace.internal.datastreams.processor import PartitionKey
from ddtrace.internal.native import DDSketch

from .utils import BOOTSTRAP_SERVERS
from .utils import GROUP_ID
from .utils import KEY
from .utils import PAYLOAD
from .utils import consumer_ctx
from .utils import create_topic
from .utils import producer_ctx


@pytest.fixture(autouse=True)
def patch_aiokafka():
    patch()
    yield
    unpatch()


@pytest.fixture
def dsm_processor():
    processor = data_streams_processor(reset=True)
    assert processor is not None, "Datastream Monitoring is not enabled"
    yield processor
    # Processor should be recreated by the tracer fixture
    processor.shutdown(timeout=5)


@pytest.mark.asyncio
async def test_data_streams_headers(dsm_processor):
    """Test that DSM pathway context headers are injected during send"""
    topic = await create_topic("data_streams_headers")

    async with producer_ctx([BOOTSTRAP_SERVERS]) as producer:
        await producer.send_and_wait(topic, value=PAYLOAD)

    async with consumer_ctx([topic]) as consumer:
        result = await consumer.getone()
        await consumer.commit()

    assert any(header[0] == PROPAGATION_KEY_BASE_64 for header in result.headers)
    assert len(dsm_processor._buckets) >= 1


@pytest.mark.asyncio
async def test_data_streams_pathway_stats(dsm_processor):
    topic = await create_topic("data_streams_pathway_stats")

    async with producer_ctx([BOOTSTRAP_SERVERS]) as producer:
        await producer.send_and_wait(topic, value=PAYLOAD, key=KEY)

    async with consumer_ctx([topic]) as consumer:
        await consumer.getone()
        await consumer.commit()

    buckets = dsm_processor._buckets
    assert len(buckets) == 1

    bucket = list(buckets.values())[0]
    pathway_stats = bucket.pathway_stats

    # Compute expected hashes based on edge tags to verify pathway continuity
    ctx = DataStreamsCtx(dsm_processor, 0, 0, 0)
    expected_producer_hash = ctx._compute_hash(
        sorted(["direction:out", f"topic:{topic}", "type:kafka"]),
        0,
    )
    expected_consumer_hash = ctx._compute_hash(
        sorted(["direction:in", f"group:{GROUP_ID}", f"topic:{topic}", "type:kafka"]),
        expected_producer_hash,
    )

    expected_producer_key = (
        f"direction:out,topic:{topic},type:kafka",
        expected_producer_hash,
        0,
    )
    expected_consumer_key = (
        f"direction:in,group:{GROUP_ID},topic:{topic},type:kafka",
        expected_consumer_hash,
        expected_producer_hash,
    )

    assert expected_producer_key in pathway_stats, "Producer checkpoint with correct hash should exist"
    assert expected_consumer_key in pathway_stats, "Consumer checkpoint with correct parent hash should exist"

    # Verify latency tracking for both checkpoints
    assert pathway_stats[expected_producer_key].full_pathway_latency.count == 1
    assert pathway_stats[expected_producer_key].edge_latency.count == 1
    assert pathway_stats[expected_consumer_key].full_pathway_latency.count == 1
    assert pathway_stats[expected_consumer_key].edge_latency.count == 1


@pytest.mark.asyncio
async def test_data_streams_offset_monitoring_auto_commit(dsm_processor):
    topic = await create_topic("data_streams_offset_monitoring_auto_commit")

    async with producer_ctx([BOOTSTRAP_SERVERS]) as producer:
        await producer.send_and_wait(topic, value=PAYLOAD, key=KEY)
        await producer.send_and_wait(topic, value=PAYLOAD, key=KEY)

    async with consumer_ctx([topic], enable_auto_commit=True) as consumer:
        msg = await consumer.getone()

    buckets = dsm_processor._buckets
    assert len(buckets) == 1

    bucket = list(buckets.values())[0]

    # Check produce offsets were tracked
    produce_offset = bucket.latest_produce_offsets.get(PartitionKey(topic, 0))
    assert produce_offset is not None and produce_offset == 1

    # Check consume offsets were tracked
    commit_key = ConsumerPartitionKey(GROUP_ID, topic, 0)
    commit_offset = bucket.latest_commit_offsets.get(commit_key)
    assert commit_offset is not None and commit_offset == msg.offset + 1


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "offsets",
    [
        None,
        {TopicPartition("data_streams_offset_monitoring_commit", 0): 2},
        {TopicPartition("data_streams_offset_monitoring_commit", 0): (2, "meta")},
    ],
)
async def test_data_streams_offset_monitoring_commit(dsm_processor, offsets):
    topic = await create_topic("data_streams_offset_monitoring_commit")

    async with producer_ctx([BOOTSTRAP_SERVERS]) as producer:
        await producer.send_and_wait(topic, value=PAYLOAD, key=KEY)
        await producer.send_and_wait(topic, value=PAYLOAD, key=KEY)

    async with consumer_ctx([topic], enable_auto_commit=False) as consumer:
        await consumer.getone()
        msg = await consumer.getone()
        await consumer.commit(offsets)

    buckets = dsm_processor._buckets
    assert len(buckets) == 1

    bucket = list(buckets.values())[0]

    # Check produce offsets were tracked
    produce_offset = bucket.latest_produce_offsets.get(PartitionKey(topic, 0))
    assert produce_offset is not None and produce_offset == 1

    # Check consume offsets were tracked
    commit_key = ConsumerPartitionKey(GROUP_ID, topic, 0)
    commit_offset = bucket.latest_commit_offsets.get(commit_key)
    assert commit_offset is not None and commit_offset == msg.offset + 1


@pytest.mark.asyncio
async def test_data_streams_getmany(dsm_processor):
    """Test that getmany processes multiple messages and creates proper checkpoints"""
    topic = await create_topic("data_streams_getmany")

    async with producer_ctx([BOOTSTRAP_SERVERS]) as producer:
        await producer.send_and_wait(topic, value=PAYLOAD, key=KEY)
        await producer.send_and_wait(topic, value=PAYLOAD, key=KEY)
        await producer.send_and_wait(topic, value=PAYLOAD, key=KEY)

    async with consumer_ctx([topic]) as consumer:
        await consumer.getmany(timeout_ms=2000)
        await consumer.commit()

    buckets = dsm_processor._buckets
    assert len(buckets) == 1

    bucket = list(buckets.values())[0]
    pathway_stats = bucket.pathway_stats

    # Should have both producer and consumer checkpoints
    producer_checkpoints = [k for k in pathway_stats.keys() if "direction:out" in k[0]]
    consumer_checkpoints = [k for k in pathway_stats.keys() if "direction:in" in k[0]]

    assert len(producer_checkpoints) == 1, "Should have one producer checkpoint"
    assert len(consumer_checkpoints) == 1, "Should have one consumer checkpoint"

    for stats in pathway_stats.values():
        assert stats.full_pathway_latency.count == 3, "Should track latency for all 3 messages"


@pytest.mark.asyncio
async def test_data_streams_payload_size_tracking(dsm_processor):
    """Test payload size calculation including all components"""
    topic = await create_topic("data_streams_payload_size")

    test_payload = b"test message with some content"
    test_key = b"test_key"
    custom_headers = [("custom-header", b"custom-value")]

    async with producer_ctx([BOOTSTRAP_SERVERS]) as producer:
        await producer.send_and_wait(topic, value=test_payload, key=test_key, headers=custom_headers)

    async with consumer_ctx([topic]) as consumer:
        message = await consumer.getone()
        await consumer.commit()

    # Calculate expected size from the actual received message:
    # - payload bytes
    # - key bytes
    # - all headers (including the DSM pathway context header that was injected)
    expected_payload_size = len(test_payload) + len(test_key)
    for h_key, h_val in message.headers:
        expected_payload_size += len(h_key)
        if h_val is not None:
            expected_payload_size += len(h_val)

    buckets = dsm_processor._buckets
    assert len(buckets) == 1

    bucket = list(buckets.values())[0]
    pathway_stats = bucket.pathway_stats

    for stats in pathway_stats.values():
        assert stats.payload_size.count >= 1

        expected_sketch = DDSketch()
        expected_sketch.add(expected_payload_size)
        assert stats.payload_size.to_proto() == expected_sketch.to_proto()


@pytest.mark.asyncio
async def test_data_streams_with_none_values(dsm_processor):
    """Test DSM handles None key without errors"""
    topic = await create_topic("data_streams_none_values")

    async with producer_ctx([BOOTSTRAP_SERVERS]) as producer:
        await producer.send_and_wait(topic, value=PAYLOAD, key=None)

    async with consumer_ctx([topic]) as consumer:
        message = await consumer.getone()
        await consumer.commit()

    buckets = dsm_processor._buckets
    assert len(buckets) >= 1
    assert any(header[0] == PROPAGATION_KEY_BASE_64 for header in message.headers)


@pytest.mark.asyncio
async def test_data_streams_multiple_topics(dsm_processor):
    """Test DSM tracks checkpoints for multiple topics correctly"""
    topic1 = await create_topic("data_streams_multi_topic_1")
    topic2 = await create_topic("data_streams_multi_topic_2")

    async with producer_ctx([BOOTSTRAP_SERVERS]) as producer:
        await producer.send_and_wait(topic1, value=PAYLOAD, key=KEY)
        await producer.send_and_wait(topic2, value=PAYLOAD, key=KEY)

    async with consumer_ctx([topic1, topic2]) as consumer:
        messages = await consumer.getmany(timeout_ms=2000)
        await consumer.commit()

    # Verify we got messages from both topics
    assert len(messages) >= 1, "Should receive messages"

    buckets = dsm_processor._buckets
    assert len(buckets) == 1

    bucket = list(buckets.values())[0]
    pathway_stats = bucket.pathway_stats

    # Extract all topics from pathway stats
    checkpoint_topics = set()
    for key in pathway_stats.keys():
        edge_tags = key[0]
        for tag in edge_tags.split(","):
            if tag.startswith("topic:"):
                checkpoint_topics.add(tag.split(":", 1)[1])

    # Both topics should be tracked
    assert topic1 in checkpoint_topics, f"Topic {topic1} should be tracked"
    assert topic2 in checkpoint_topics, f"Topic {topic2} should be tracked"


@pytest.mark.snapshot(ignores=["metrics.kafka.message_offset"])
@pytest.mark.subprocess(env={"DD_DATA_STREAMS_ENABLED": "true"}, ddtrace_run=True, err=None)
def test_data_streams_aiokafka_enabled():
    """Test that verifies DSM is enabled and adds dd-pathway-ctx-base64 header to aiokafka messages."""
    import asyncio

    from aiokafka import AIOKafkaConsumer
    from aiokafka import AIOKafkaProducer
    from aiokafka.admin import AIOKafkaAdminClient
    from aiokafka.admin import NewTopic

    from tests.contrib.config import KAFKA_CONFIG

    BOOTSTRAP_SERVERS = f"127.0.0.1:{KAFKA_CONFIG['port']}"
    topic_name = "test_data_streams_aiokafka_enabled"

    async def _test():
        admin = AIOKafkaAdminClient(bootstrap_servers=[BOOTSTRAP_SERVERS])
        await admin.start()
        try:
            await admin.create_topics([NewTopic(topic_name, 1, 1)])
        except Exception:
            pass
        finally:
            await admin.close()

        producer = AIOKafkaProducer(bootstrap_servers=[BOOTSTRAP_SERVERS])
        await producer.start()

        consumer = AIOKafkaConsumer(
            topic_name, bootstrap_servers=[BOOTSTRAP_SERVERS], group_id="test_group", auto_offset_reset="earliest"
        )
        await consumer.start()

        try:
            await producer.send_and_wait(topic_name, value=b"test")

            import time

            time.sleep(0.5)
            message = await consumer.getone()
            assert "dd-pathway-ctx-base64" in [h[0] for h in message.headers]

        finally:
            await consumer.stop()
            await producer.stop()

    asyncio.run(_test())


async def test_data_streams_headers_edge_cases(dsm_processor):
    """Test DSM handles non-UTF8 and None-valued headers without crashing"""
    topic = await create_topic("data_streams_headers_edge_cases")

    bad_bytes = b"\xff\xfe\xfa"  # invalid UTF-8
    headers = [("non-utf8", bad_bytes), ("none-header", None)]

    async with producer_ctx([BOOTSTRAP_SERVERS]) as producer:
        await producer.send_and_wait(topic, value=PAYLOAD, key=KEY, headers=headers)

    async with consumer_ctx([topic]) as consumer:
        message = await consumer.getone()
        await consumer.commit()

    # Verify message carries our edge case headers
    header_keys = [k for k, _ in (message.headers or [])]
    assert "non-utf8" in header_keys, "Non-UTF8 header should be present"
    assert "none-header" in header_keys, "None-valued header should be present"
