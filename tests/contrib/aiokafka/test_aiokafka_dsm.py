from aiokafka.structs import TopicPartition
import mock
import pytest

from ddtrace.contrib.internal.aiokafka.patch import patch
from ddtrace.contrib.internal.aiokafka.patch import unpatch
from ddtrace.internal.datastreams.processor import PROPAGATION_KEY_BASE_64
from ddtrace.internal.datastreams.processor import ConsumerPartitionKey
from ddtrace.internal.datastreams.processor import PartitionKey
from tests.utils import DummyTracer
from tests.utils import override_global_tracer

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
def tracer():
    tracer = DummyTracer()
    with override_global_tracer(tracer):
        yield tracer
        tracer.flush()


@pytest.fixture
def dsm_processor(tracer):
    processor = tracer.data_streams_processor
    with mock.patch("ddtrace.internal.datastreams.data_streams_processor", return_value=processor):
        yield processor
        # flush buckets for the next test run
        processor.periodic()


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

    try:
        del dsm_processor._current_context.value
    except AttributeError:
        pass

    async with producer_ctx([BOOTSTRAP_SERVERS]) as producer:
        await producer.send_and_wait(topic, value=PAYLOAD, key=KEY)

    async with consumer_ctx([topic]) as consumer:
        await consumer.getone()
        await consumer.commit()

    buckets = dsm_processor._buckets
    assert len(buckets) == 1

    bucket = list(buckets.values())[0]
    pathway_stats = bucket.pathway_stats

    producer_checkpoints = [k for k in pathway_stats.keys() if "direction:out" in k[0]]
    assert len(producer_checkpoints) == 1, "Producer checkpoint should exist"

    consumer_checkpoints = [k for k in pathway_stats.keys() if "direction:in" in k[0]]
    assert len(consumer_checkpoints) == 1

    for key in pathway_stats.keys():
        stats = pathway_stats[key]
        assert stats.full_pathway_latency.count >= 1
        assert stats.edge_latency.count >= 1


@pytest.mark.asyncio
async def test_data_streams_offset_monitoring_auto_commit(dsm_processor):
    topic = await create_topic("data_streams_offset_monitoring_auto_commit")

    try:
        del dsm_processor._current_context.value
    except AttributeError:
        pass

    async with producer_ctx([BOOTSTRAP_SERVERS]) as producer:
        await producer.send_and_wait(topic, value=PAYLOAD, key=KEY)
        await producer.send_and_wait(topic, value=PAYLOAD, key=KEY)

    async with consumer_ctx([topic], enable_auto_commit=True) as consumer:
        message1 = await consumer.getone()

    buckets = dsm_processor._buckets
    assert len(buckets) == 1

    bucket = list(buckets.values())[0]

    assert len(bucket.latest_produce_offsets) > 0
    produce_offset = bucket.latest_produce_offsets.get(PartitionKey(topic, 0))
    assert produce_offset is not None and produce_offset >= 0

    commit_offset = bucket.latest_commit_offsets.get(ConsumerPartitionKey(GROUP_ID, topic, 0))
    assert commit_offset is not None
    assert commit_offset == message1.offset + 1


@pytest.mark.asyncio
async def test_data_streams_offset_monitoring_manual_commit(dsm_processor):
    topic = await create_topic("data_streams_offset_monitoring_manual_commit")

    try:
        del dsm_processor._current_context.value
    except AttributeError:
        pass

    async with producer_ctx([BOOTSTRAP_SERVERS]) as producer:
        await producer.send_and_wait(topic, value=PAYLOAD, key=KEY)
        await producer.send_and_wait(topic, value=PAYLOAD, key=KEY)

    async with consumer_ctx([topic], enable_auto_commit=False) as consumer:
        await consumer.getone()
        message2 = await consumer.getone()
        await consumer.commit({TopicPartition(message2.topic, message2.partition): message2.offset + 1})

    buckets = dsm_processor._buckets
    assert len(buckets) == 1

    bucket = list(buckets.values())[0]

    produce_offset = bucket.latest_produce_offsets.get(PartitionKey(topic, 0))
    assert produce_offset is not None and produce_offset >= 1

    commit_offset = bucket.latest_commit_offsets.get(ConsumerPartitionKey(GROUP_ID, topic, 0))
    assert commit_offset is not None
    assert commit_offset == message2.offset + 1


@pytest.mark.asyncio
async def test_data_streams_offset_monitoring_manual_commit_tuple(dsm_processor):
    topic = await create_topic("data_streams_offset_monitoring_manual_commit_tuple")

    try:
        del dsm_processor._current_context.value
    except AttributeError:
        pass

    async with producer_ctx([BOOTSTRAP_SERVERS]) as producer:
        await producer.send_and_wait(topic, value=PAYLOAD, key=KEY)
        await producer.send_and_wait(topic, value=PAYLOAD, key=KEY)

    async with consumer_ctx([topic], enable_auto_commit=False) as consumer:
        # Read two messages, commit using tuple (offset, metadata)
        await consumer.getone()
        message2 = await consumer.getone()
        await consumer.commit({TopicPartition(message2.topic, message2.partition): (message2.offset + 1, "meta")})

    buckets = dsm_processor._buckets
    assert len(buckets) == 1

    bucket = list(buckets.values())[0]

    # latest commit offset should reflect the value from the tuple
    commit_key = ConsumerPartitionKey(GROUP_ID, topic, 0)
    commit_offset = bucket.latest_commit_offsets.get(commit_key)
    assert commit_offset is not None
    assert commit_offset == message2.offset + 1


@pytest.mark.asyncio
async def test_data_streams_getmany(dsm_processor):
    topic = await create_topic("data_streams_getmany")

    try:
        del dsm_processor._current_context.value
    except AttributeError:
        pass

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

    consumer_checkpoints = [k for k in pathway_stats.keys() if "direction:in" in k[0]]
    assert len(consumer_checkpoints) >= 1


@pytest.mark.asyncio
async def test_data_streams_payload_size_tracking(dsm_processor):
    """Test exact payload size calculation including custom headers"""
    topic = await create_topic("data_streams_payload_size")

    try:
        del dsm_processor._current_context.value
    except AttributeError:
        pass

    test_payload = b"test message with some content"
    test_key = b"test_key"
    custom_headers = [("custom-header", b"custom-value")]

    # Calculate expected size:
    # - payload bytes
    # - key bytes
    # - custom headers (key + value)
    # - DSM header key (PROPAGATION_KEY_BASE_64)
    # - DSM header value (pathway context, ~28 bytes)
    expected_payload_size = len(test_payload) + len(test_key)
    for h_key, h_val in custom_headers:
        expected_payload_size += len(h_key) + len(h_val)
    expected_payload_size += len(PROPAGATION_KEY_BASE_64)  # DSM header key
    expected_payload_size += 28  # DSM header value (pathway context)

    async with producer_ctx([BOOTSTRAP_SERVERS]) as producer:
        await producer.send_and_wait(topic, value=test_payload, key=test_key, headers=custom_headers)

    async with consumer_ctx([topic]) as consumer:
        await consumer.getone()
        await consumer.commit()

    buckets = dsm_processor._buckets
    assert len(buckets) == 1

    bucket = list(buckets.values())[0]
    pathway_stats = bucket.pathway_stats

    for key, stats in pathway_stats.items():
        assert stats.payload_size.count >= 1
        assert stats.payload_size.sum == expected_payload_size


@pytest.mark.asyncio
async def test_data_streams_with_none_values(dsm_processor):
    topic = await create_topic("data_streams_none_values")

    try:
        del dsm_processor._current_context.value
    except AttributeError:
        pass

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
    topic1 = await create_topic("data_streams_multi_topic_1")
    topic2 = await create_topic("data_streams_multi_topic_2")

    try:
        del dsm_processor._current_context.value
    except AttributeError:
        pass

    async with producer_ctx([BOOTSTRAP_SERVERS]) as producer:
        await producer.send_and_wait(topic1, value=PAYLOAD, key=KEY)
        await producer.send_and_wait(topic2, value=PAYLOAD, key=KEY)

    async with consumer_ctx([topic1, topic2]) as consumer:
        await consumer.getmany(timeout_ms=2000)
        await consumer.commit()

    buckets = dsm_processor._buckets
    assert len(buckets) >= 1

    bucket = list(buckets.values())[0]
    pathway_stats = bucket.pathway_stats

    checkpoint_topics = set()
    for key in pathway_stats.keys():
        edge_tags = key[0]
        if "topic:" in edge_tags:
            for tag in edge_tags.split(","):
                if tag.startswith("topic:"):
                    checkpoint_topics.add(tag.split(":", 1)[1])

    assert topic1 in checkpoint_topics or topic2 in checkpoint_topics


@pytest.mark.asyncio
async def test_data_streams_headers_edge_cases(dsm_processor):
    """Non-UTF8 header bytes should decode with errors='ignore'; None-valued headers are ignored."""
    topic = await create_topic("data_streams_headers_edge_cases")

    try:
        del dsm_processor._current_context.value
    except AttributeError:
        pass

    bad_bytes = b"\xff\xfe\xfa"  # invalid UTF-8
    headers = [("non-utf8", bad_bytes), ("none-header", None)]

    async with producer_ctx([BOOTSTRAP_SERVERS]) as producer:
        await producer.send_and_wait(topic, value=PAYLOAD, key=KEY, headers=headers)

    async with consumer_ctx([topic]) as consumer:
        message = await consumer.getone()
        await consumer.commit()

    # Sanity: message should carry our headers (Kafka allows None header values)
    assert any(k == "non-utf8" for k, _ in (message.headers or []))
    assert any(k == "none-header" for k, _ in (message.headers or []))

    # DSM should have processed without crashing and created buckets/stats
    buckets = dsm_processor._buckets
    assert len(buckets) >= 1
    bucket = list(buckets.values())[0]
    assert len(bucket.pathway_stats) >= 1
