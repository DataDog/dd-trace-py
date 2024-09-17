import logging

import aiokafka
from aiokafka.admin import AIOKafkaAdminClient
from aiokafka.admin import NewTopic
from aiokafka.structs import TopicPartition
import mock
import pytest

from ddtrace import Pin
from ddtrace import Tracer
from ddtrace.contrib.aiokafka.patch import patch
from ddtrace.contrib.aiokafka.patch import unpatch
import ddtrace.internal.datastreams  # noqa: F401 - used as part of mock patching
from ddtrace.internal.datastreams.processor import DataStreamsCtx
from tests.contrib.config import KAFKA_CONFIG
from tests.datastreams.test_public_api import MockedTracer
from tests.utils import DummyTracer
from tests.utils import override_config


logger = logging.getLogger(__name__)


GROUP_ID = "test_group"
BOOTSTRAP_SERVERS = "127.0.0.1:{}".format(KAFKA_CONFIG["port"])
KEY = "test_key".encode("utf-8")
PAYLOAD = "hueh hueh hueh".encode("utf-8")
ENABLE_AUTO_COMMIT = True


@pytest.fixture()
async def kafka_topic(request):
    topic_name = request.node.name.replace("[", "_").replace("]", "")
    await create_topic(topic_name)
    return topic_name


@pytest.fixture()
async def kafka_topic_2(request):
    topic_name = request.node.name.replace("[", "_").replace("]", "") + "_2"
    await create_topic(topic_name)
    return topic_name


async def create_topic(topic_name):
    logger.debug("Creating topic %s", topic_name)

    client = AIOKafkaAdminClient(bootstrap_servers=[BOOTSTRAP_SERVERS])
    await client.start()

    try:
        await client.delete_topics([topic_name])
        await client.create_topics([NewTopic(topic_name, 1, 1)])
    except Exception as e:
        logger.error("Failed to delete/create topic %s: %s", topic_name, e)
    finally:
        await client.close()

    return topic_name


@pytest.fixture
async def dummy_tracer():
    patch()
    t = DummyTracer()
    # disable backoff because it makes these tests less reliable
    t._writer._send_payload_with_backoff = t._writer._send_payload
    yield t
    unpatch()


@pytest.fixture
async def tracer():
    patch()
    t = Tracer()
    # disable backoff because it makes these tests less reliable
    t._writer._send_payload_with_backoff = t._writer._send_payload

    try:
        yield t
    finally:
        t.flush()
        t.shutdown()
        unpatch()


@pytest.fixture
async def producer(tracer):
    _producer = aiokafka.AIOKafkaProducer(bootstrap_servers=[BOOTSTRAP_SERVERS])
    await _producer.start()
    Pin.override(_producer, tracer=tracer)
    yield _producer
    await _producer.stop()


@pytest.fixture
def enable_auto_commit():
    yield ENABLE_AUTO_COMMIT


@pytest.fixture
async def consumer(tracer, kafka_topic, enable_auto_commit):
    _consumer = get_consumer(enable_auto_commit)
    Pin.override(_consumer, tracer=tracer)
    _consumer.subscribe([kafka_topic])
    await _consumer.start()
    yield _consumer
    await _consumer.stop()


def get_consumer(enable_auto_commit=ENABLE_AUTO_COMMIT):
    consumer = aiokafka.AIOKafkaConsumer(
        bootstrap_servers=[BOOTSTRAP_SERVERS],
        auto_offset_reset="earliest",
        group_id=GROUP_ID,
        enable_auto_commit=enable_auto_commit,
    )
    return consumer


@pytest.fixture
def dsm_processor(tracer):
    processor = tracer.data_streams_processor
    with mock.patch("ddtrace.internal.datastreams.data_streams_processor", return_value=processor):
        yield processor


async def test_send_single_server(dummy_tracer, producer, kafka_topic):
    Pin.override(producer, tracer=dummy_tracer)
    await producer.send_and_wait(kafka_topic, value=PAYLOAD, key=KEY)

    traces = dummy_tracer.pop_traces()
    assert 1 == len(traces)
    produce_span = traces[0][0]
    assert produce_span.get_tag("messaging.kafka.bootstrap.servers") == BOOTSTRAP_SERVERS
    Pin.override(producer, tracer=None)


async def test_send_multiple_servers(dummy_tracer, kafka_topic):
    producer = aiokafka.AIOKafkaProducer(bootstrap_servers=[BOOTSTRAP_SERVERS] * 3)
    await producer.start()
    Pin.override(producer, tracer=dummy_tracer)
    await producer.send_and_wait(kafka_topic, value=PAYLOAD, key=KEY)
    await producer.stop()

    traces = dummy_tracer.pop_traces()
    assert 1 == len(traces)
    produce_span = traces[0][0]
    assert produce_span.get_tag("messaging.kafka.bootstrap.servers") == ",".join([BOOTSTRAP_SERVERS] * 3)
    Pin.override(producer, tracer=None)


async def test_send_none_key(dummy_tracer, producer, kafka_topic):
    Pin.override(producer, tracer=dummy_tracer)
    await producer.send_and_wait(kafka_topic, value=PAYLOAD, key=None)

    traces = dummy_tracer.pop_traces()
    assert 1 == len(traces), "key=None does not cause send() call to raise an exception"
    Pin.override(producer, tracer=None)


@pytest.mark.parametrize("tombstone", [False, True])
@pytest.mark.snapshot(ignores=["metrics.kafka.message_offset"])
async def test_send_and_wait_message(producer, tombstone, kafka_topic):
    if tombstone:
        await producer.send_and_wait(kafka_topic, value=None, key=KEY)
    else:
        await producer.send_and_wait(kafka_topic, value=PAYLOAD, key=KEY)


@pytest.mark.snapshot(ignores=["metrics.kafka.message_offset"])
async def test_send_getone_with_commit(producer, consumer, kafka_topic):
    fut = await producer.send(kafka_topic, value=PAYLOAD, key=KEY)
    await fut
    await producer.stop()
    result = await consumer.getone()
    await consumer.commit()
    assert result.key == KEY
    assert result.value == PAYLOAD


@pytest.mark.snapshot(ignores=["metrics.kafka.message_offset"])
async def test_send_getone_with_commit_with_offset(producer, consumer, kafka_topic):
    fut = await producer.send(kafka_topic, value=PAYLOAD, key=KEY)
    await fut
    await producer.stop()
    result = await consumer.getone()
    await consumer.commit({TopicPartition(result.topic, result.partition): result.offset + 1})
    assert result.key == KEY
    assert result.value == PAYLOAD


@pytest.mark.snapshot(ignores=["metrics.kafka.message_offset"])
async def test_send_and_wait_getone_with_commit(producer, consumer, kafka_topic):
    await producer.send_and_wait(kafka_topic, value=PAYLOAD, key=KEY)
    await producer.stop()
    result = await consumer.getone()
    await consumer.commit()
    assert result.key == KEY
    assert result.value == PAYLOAD


@pytest.mark.snapshot(ignores=["metrics.kafka.message_offset"])
async def test_send_and_wait_getone_with_commit_with_offset(producer, consumer, kafka_topic):
    await producer.send_and_wait(kafka_topic, value=PAYLOAD, key=KEY)
    await producer.stop()
    result = await consumer.getone()
    await consumer.commit({TopicPartition(result.topic, result.partition): result.offset + 1})
    assert result.key == KEY
    assert result.value == PAYLOAD


@pytest.mark.snapshot(ignores=["metrics.kafka.message_offset"])
async def test_getmany_single_message_with_commit(producer, tracer, kafka_topic):
    await producer.send_and_wait(kafka_topic, value=PAYLOAD, key=KEY)
    await producer.stop()

    consumer = get_consumer()
    Pin.override(consumer, tracer=tracer)
    consumer.subscribe([kafka_topic])
    await consumer.start()
    messages = await consumer.getmany(timeout_ms=1000)
    assert len(messages) == 1
    await consumer.commit()
    await consumer.stop()


@pytest.mark.snapshot(ignores=["metrics.kafka.message_offset"])
async def test_getmany_multiple_messages_with_commit(producer, tracer, kafka_topic):
    await producer.send_and_wait(kafka_topic, value=PAYLOAD)
    await producer.send_and_wait(kafka_topic, value=PAYLOAD)
    await producer.stop()

    consumer = get_consumer()
    Pin.override(consumer, tracer=tracer)
    consumer.subscribe([kafka_topic])
    await consumer.start()
    messages = await consumer.getmany(timeout_ms=1000)
    assert len(messages) == 1
    for tp, records in messages.items():
        assert len(records) == 2
    await consumer.commit()
    await consumer.stop()


@pytest.mark.snapshot(ignores=["metrics.kafka.message_offset"])
async def test_getmany_multiple_messages_multiple_topics(producer, tracer, kafka_topic, kafka_topic_2):
    await producer.send_and_wait(kafka_topic, PAYLOAD)
    await producer.send_and_wait(kafka_topic, PAYLOAD)
    await producer.send_and_wait(kafka_topic_2, PAYLOAD)
    await producer.stop()

    consumer = get_consumer()
    Pin.override(consumer, tracer=tracer)
    consumer.subscribe([kafka_topic, kafka_topic_2])
    await consumer.start()
    try:
        results = await consumer.getmany(timeout_ms=1000)
        assert len(results) == 2
        for tp, records in results.items():
            if tp.topic == kafka_topic:
                assert len(records) == 2
            if tp.topic == kafka_topic_2:
                assert len(records) == 1
    finally:
        await consumer.stop()


async def test_getmany_multiple_messages_multiple_topics_distributed_tracing(
    producer, dummy_tracer, kafka_topic, kafka_topic_2
):
    Pin.override(producer, tracer=dummy_tracer)
    with override_config("aiokafka", dict(distributed_tracing_enabled=True)):
        await producer.send_and_wait(kafka_topic, PAYLOAD)
        await producer.send_and_wait(kafka_topic, PAYLOAD)
        await producer.send_and_wait(kafka_topic_2, PAYLOAD)
        await producer.stop()

        consumer = get_consumer()
        Pin.override(consumer, tracer=dummy_tracer)
        consumer.subscribe([kafka_topic, kafka_topic_2])
        await consumer.start()
        try:
            results = await consumer.getmany(timeout_ms=1000)
            assert len(results) == 2
            # Check that all received message on all topics have the headers set
            for tp, records in results.items():
                if tp.topic == kafka_topic:
                    assert len(records) == 2
                    for record in records:
                        header_found = False
                        for header in record.headers:
                            if header[0] == "x-datadog-trace-id":
                                header_found = True
                        assert header_found is True
                if tp.topic == kafka_topic_2:
                    assert len(records) == 1
                    for record in records:
                        header_found = False
                        for header in record.headers:
                            if header[0] == "x-datadog-trace-id":
                                header_found = True
                        assert header_found is True

            # Check that the traces span id and parent id match
            traces = dummy_tracer.pop_traces()
            assert 6 == len(traces)
            for x in range(3, 6):
                # consume span
                consume_span = traces[x][0]
                # parent found?
                found_parent = False
                for y in range(3):
                    if consume_span.parent_id == traces[y][0].span_id:
                        found_parent = True
                assert found_parent is True
        finally:
            await consumer.stop()


@pytest.mark.snapshot(ignores=["metrics.kafka.message_offset"])
async def test_distributed_tracing_headers_disabled(producer, consumer, kafka_topic, tracer):
    with override_config("aiokafka", dict(distributed_tracing_enabled=False)):
        await producer.send_and_wait(kafka_topic, value=PAYLOAD, key=KEY)
        await producer.stop()
        result = await consumer.getone()
        await consumer.commit()

        propagation_asserted = False
        for header in result.headers:
            if header[0] == "x-datadog-trace-id":
                propagation_asserted = True

        assert propagation_asserted is False


@pytest.mark.snapshot(ignores=["metrics.kafka.message_offset", "meta.tracestate"])
async def test_distributed_tracing_headers_enabled(producer, consumer, kafka_topic, tracer):
    with override_config("aiokafka", dict(distributed_tracing_enabled=True)):
        await producer.send_and_wait(kafka_topic, value=PAYLOAD, key=KEY)
        await producer.stop()
        result = await consumer.getone()
        await consumer.commit()

        propagation_asserted = False
        for header in result.headers:
            if header[0] == "x-datadog-trace-id":
                propagation_asserted = True

        assert propagation_asserted is True


@pytest.mark.snapshot(ignores=["metrics.kafka.message_offset", "meta.tracestate"])
async def test_distributed_tracing_headers_with_additional_headers(producer, consumer, kafka_topic, tracer):
    with override_config("aiokafka", dict(distributed_tracing_enabled=True)):
        await producer.send_and_wait(
            kafka_topic, value=PAYLOAD, key=KEY, headers=[("some_header", "some_value".encode("utf-8"))]
        )
        await producer.stop()
        result = await consumer.getone()
        await consumer.commit()

        propagation_asserted = False
        for header in result.headers:
            if header[0] == "x-datadog-trace-id":
                propagation_asserted = True

        assert propagation_asserted is True


async def test_distributed_tracing_parent_span(producer, consumer, kafka_topic, dummy_tracer):
    Pin.override(producer, tracer=dummy_tracer)
    Pin.override(consumer, tracer=dummy_tracer)
    with override_config("aiokafka", dict(distributed_tracing_enabled=True)):
        await producer.send_and_wait(
            kafka_topic, value=PAYLOAD, key=KEY, headers=[("some_header", "some_value".encode("utf-8"))]
        )
        await producer.stop()
        await consumer.getone()
        await consumer.commit()

        traces = dummy_tracer.pop_traces()

        assert 2 == len(traces)

        send_and_wait_span = traces[0][0]
        getone_span = traces[1][0]

        assert getone_span.parent_id == send_and_wait_span.span_id


@pytest.mark.snapshot(ignores=["metrics.kafka.message_offset"])
async def test_data_streams_headers(producer, consumer, kafka_topic, tracer):
    await producer.send_and_wait(kafka_topic, value=PAYLOAD, key=KEY)
    await producer.stop()
    result = await consumer.getone()
    await consumer.commit()

    assert 1 == len(result.headers)
    assert "dd-pathway-ctx-base64" == result.headers[0][0]


@pytest.mark.parametrize("distributed_tracing_enabled", [True, False])
@pytest.mark.parametrize("enable_auto_commit", [True, False])
async def test_data_streams_kafka_pathways(
    dsm_processor, consumer, producer, kafka_topic, distributed_tracing_enabled, enable_auto_commit
):
    with override_config("aiokafka", dict(distributed_tracing_enabled=distributed_tracing_enabled)):
        try:
            del dsm_processor._current_context.value
        except AttributeError:
            pass

        await producer.send_and_wait(kafka_topic, value=PAYLOAD, key=KEY)
        await producer.stop()
        await consumer.getone()
        await consumer.commit()

        buckets = dsm_processor._buckets
        assert len(buckets) == 1
        first = list(buckets.values())[0].pathway_stats

        ctx = DataStreamsCtx(MockedTracer().data_streams_processor, 0, 0, 0)
        parent_hash = ctx._compute_hash(sorted(["direction:out", "type:kafka", "topic:{}".format(kafka_topic)]), 0)
        child_hash = ctx._compute_hash(
            sorted(["direction:in", "type:kafka", "group:test_group", "topic:{}".format(kafka_topic)]), parent_hash
        )

        assert (
            first[("direction:out,topic:{},type:kafka".format(kafka_topic), parent_hash, 0)].full_pathway_latency.count
            >= 1
        )
        assert first[("direction:out,topic:{},type:kafka".format(kafka_topic), parent_hash, 0)].edge_latency.count >= 1
        assert (
            first[
                (
                    "direction:in,group:test_group,topic:{},type:kafka".format(kafka_topic),
                    child_hash,
                    parent_hash,
                )
            ].full_pathway_latency.count
            >= 1
        )
        assert (
            first[
                (
                    "direction:in,group:test_group,topic:{},type:kafka".format(kafka_topic),
                    child_hash,
                    parent_hash,
                )
            ].edge_latency.count
            >= 1
        )


@pytest.mark.parametrize("distributed_tracing_enabled", [True, False])
async def test_data_streams_kafka_commit_offsets(
    dsm_processor, consumer, producer, kafka_topic, distributed_tracing_enabled
):
    with override_config("aiokafka", dict(distributed_tracing_enabled=distributed_tracing_enabled)):
        try:
            del dsm_processor._current_context.value
        except AttributeError:
            pass

        await producer.send_and_wait(kafka_topic, value=PAYLOAD, key=KEY)
        await producer.stop()
        result = await consumer.getone()
        await consumer.commit({TopicPartition(result.topic, result.partition): result.offset + 1})

        buckets = dsm_processor._buckets
        assert len(buckets) == 1
        offsets = list(buckets.values())[0].latest_commit_offsets
        first = next(iter(offsets.items()))
        assert GROUP_ID == first[0][0]
        assert kafka_topic == first[0][1]
        assert result.partition == first[0][2]
        assert result.offset + 1 == first[1]


@pytest.mark.parametrize("distributed_tracing_enabled", [True, False])
async def test_data_streams_kafka_produce_offsets(
    dsm_processor, consumer, producer, kafka_topic, distributed_tracing_enabled
):
    with override_config("aiokafka", dict(distributed_tracing_enabled=distributed_tracing_enabled)):
        try:
            del dsm_processor._current_context.value
        except AttributeError:
            pass

        result = await producer.send_and_wait(kafka_topic, value=PAYLOAD, key=KEY)
        await producer.stop()

        buckets = dsm_processor._buckets
        assert len(buckets) == 1
        offsets = list(buckets.values())[0].latest_produce_offsets
        first = next(iter(offsets.items()))
        assert kafka_topic == first[0][0]
        assert result.partition == first[0][1]
        assert result.offset == first[1]


@pytest.mark.parametrize("distributed_tracing_enabled", [True, False])
async def test_data_streams_kafka_send_api_compatibility(
    dsm_processor, producer, kafka_topic, distributed_tracing_enabled
):
    with override_config("aiokafka", dict(distributed_tracing_enabled=distributed_tracing_enabled)):
        PAYLOAD = bytes("data streams", encoding="utf-8")
        KEY = bytes("test_key", encoding="utf-8")
        try:
            del dsm_processor._current_context.value
        except AttributeError:
            pass

        # All of these should work
        await producer.send_and_wait(kafka_topic, PAYLOAD)
        await producer.send_and_wait(kafka_topic, value=PAYLOAD)
        await producer.send_and_wait(kafka_topic, PAYLOAD, key=KEY)
        await producer.send_and_wait(kafka_topic, value=PAYLOAD, key=KEY)
        await producer.send_and_wait(kafka_topic, key=KEY)
        await producer.stop()

        buckets = dsm_processor._buckets
        assert len(buckets) == 1
        bucket = list(buckets.values())[0]
        assert bucket.latest_produce_offsets[(kafka_topic, 0)] == 4
