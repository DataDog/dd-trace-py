import kafka
from kafka.structs import OffsetAndMetadata
import pytest

from ddtrace import Pin
import ddtrace.internal.datastreams  # noqa: F401 - used as part of mock patching
from tests.contrib.config import KAFKA_CONFIG
from tests.utils import override_config


GROUP_ID = "test_group"
BOOTSTRAP_SERVERS = "localhost:{}".format(KAFKA_CONFIG["port"])
KEY = bytes("test_key", encoding="utf-8")
PAYLOAD = bytes("hueh hueh hueh", encoding="utf-8")
DSM_TEST_PATH_HEADER_SIZE = 28
print("running test")


@pytest.fixture()
def kafka_topic(request):
    # todo: add a UUID, but it makes snapshot tests fail.
    topic_name = request.node.name.replace("[", "_").replace("]", "")

    client = kafka.KafkaAdminClient(bootstrap_servers=[BOOTSTRAP_SERVERS])
    try:
        client.create_topics([kafka.admin.NewTopic(topic_name, 1, 1)])
    except kafka.errors.TopicAlreadyExistsError:
        pass
    return topic_name


@pytest.fixture
def producer(tracer):
    _producer = kafka.KafkaProducer(bootstrap_servers=[BOOTSTRAP_SERVERS])
    Pin.override(_producer, tracer=tracer)
    return _producer


@pytest.fixture
def consumer_with_topic(tracer, kafka_topic):
    print("Connecting to kafka")
    _consumer = kafka.KafkaConsumer(
        bootstrap_servers=[BOOTSTRAP_SERVERS], auto_offset_reset="earliest", group_id=GROUP_ID, enable_auto_commit=False
    )
    _consumer.subscribe(topics=[kafka_topic])
    Pin.override(_consumer, tracer=tracer)
    return _consumer


def consumer_without_topic(tracer):
    print("Connecting to kafka")
    _consumer = kafka.KafkaConsumer(
        bootstrap_servers=[BOOTSTRAP_SERVERS], auto_offset_reset="earliest", group_id=GROUP_ID, enable_auto_commit=False
    )
    Pin.override(_consumer, tracer=tracer)
    return _consumer


def test_commit(producer, consumer_with_topic, kafka_topic):
    print("running tests")
    with override_config("kafka", dict(trace_empty_poll_enabled=False)):
        producer.send(kafka_topic, value=PAYLOAD, key=KEY)
        producer.flush()
        result = consumer_with_topic.poll(1000)
        for topic_partition in result:
            for record in result[topic_partition]:
                consumer_with_topic.commit({topic_partition: OffsetAndMetadata(record.offset, "")})
