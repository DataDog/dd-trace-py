import json
import logging

import aiokafka
from aiokafka.admin import AIOKafkaAdminClient
from aiokafka.admin import NewTopic
from aiokafka.structs import TopicPartition
import pytest

from ddtrace import Pin
from ddtrace import Tracer
from ddtrace.contrib.aiokafka.patch import patch
from ddtrace.contrib.aiokafka.patch import unpatch
from tests.contrib.config import KAFKA_CONFIG
from tests.utils import DummyTracer
from tests.utils import override_config


logger = logging.getLogger(__name__)


GROUP_ID = "test_group"
BOOTSTRAP_SERVERS = "127.0.0.1:{}".format(KAFKA_CONFIG["port"])
KEY = bytes("test_key", encoding="utf-8")
PAYLOAD = "hueh hueh hueh"


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


def serializer(value):
    return json.dumps(value).encode()


@pytest.fixture
async def producer(tracer):
    _producer = aiokafka.AIOKafkaProducer(bootstrap_servers=[BOOTSTRAP_SERVERS], value_serializer=serializer)
    await _producer.start()
    Pin.override(_producer, tracer=tracer)
    yield _producer
    await _producer.stop()


@pytest.fixture
async def consumer(tracer, kafka_topic):
    _consumer = get_consumer()
    Pin.override(_consumer, tracer=tracer)
    _consumer.subscribe([kafka_topic])
    await _consumer.start()
    yield _consumer
    await _consumer.stop()


def get_consumer():
    consumer = aiokafka.AIOKafkaConsumer(
        bootstrap_servers=[BOOTSTRAP_SERVERS], auto_offset_reset="earliest", group_id=GROUP_ID
    )
    return consumer


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
    await producer.send_and_wait(kafka_topic, value=PAYLOAD.encode("utf-8"), key=KEY)
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
async def test_message(producer, tombstone, kafka_topic):
    with override_config("kafka", dict(trace_empty_poll_enabled=False)):
        if tombstone:
            await producer.send_and_wait(kafka_topic, value=None, key=KEY)
        else:
            await producer.send_and_wait(kafka_topic, value=PAYLOAD, key=KEY)


@pytest.mark.snapshot(ignores=["metrics.kafka.message_offset"])
async def test_getone_with_commit(producer, consumer, kafka_topic):
    await producer.send_and_wait(kafka_topic, value=PAYLOAD, key=KEY)
    await producer.stop()
    await consumer.getone()
    await consumer.commit()


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
    await producer.send_and_wait(kafka_topic, value="first message")
    await producer.send_and_wait(kafka_topic, value="second message")
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
async def test_getone_with_commit_with_offset(producer, consumer, kafka_topic):
    await producer.send_and_wait(kafka_topic, value=PAYLOAD, key=KEY)
    await producer.stop()
    result = await consumer.getone()
    await consumer.commit({TopicPartition(result.topic, result.partition): result.offset + 1})


@pytest.mark.snapshot(ignores=["metrics.kafka.message_offset"])
async def test_getmany_multiple_messages_multiple_topics(producer, tracer, kafka_topic, kafka_topic_2):
    await producer.send_and_wait(kafka_topic, "1")
    await producer.send_and_wait(kafka_topic, "2")
    await producer.send_and_wait(kafka_topic_2, "3")
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
