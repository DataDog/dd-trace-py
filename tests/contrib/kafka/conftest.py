import confluent_kafka
from confluent_kafka import KafkaException
from confluent_kafka import TopicPartition
from confluent_kafka import admin as kafka_admin
import pytest

from ddtrace import config
from ddtrace._trace.filters import TraceFilter
from ddtrace._trace.pin import Pin
from ddtrace.contrib.internal.kafka.patch import patch
from ddtrace.contrib.internal.kafka.patch import unpatch
from ddtrace.trace import tracer as ddtracer
from tests.conftest import get_original_test_name
from tests.contrib.config import KAFKA_CONFIG
from tests.utils import DummyTracer


GROUP_ID = "test_group"
BOOTSTRAP_SERVERS = "{}:{}".format(KAFKA_CONFIG["host"], KAFKA_CONFIG["port"])
KEY = "test_key"


def async_producer_callback(_exception, _message):
    """Ignore async callbacks to not fail producer tests due to no error handling."""
    pass


class KafkaConsumerPollFilter(TraceFilter):
    def process_trace(self, trace):
        # Filter out all poll spans that have no received message
        if trace[0].name == "kafka.consume" and trace[0].get_tag("kafka.received_message") == "False":
            return None

        return trace


@pytest.fixture()
def kafka_topic(request):
    # todo: add a UUID, but it makes snapshot tests fail.
    topic_name = get_original_test_name(request).replace("[", "_").replace("]", "")

    client = kafka_admin.AdminClient({"bootstrap.servers": BOOTSTRAP_SERVERS})
    for _, future in client.create_topics([kafka_admin.NewTopic(topic_name, 1, 1)]).items():
        try:
            future.result()
        except KafkaException:
            pass  # The topic likely already exists
    yield topic_name


@pytest.fixture()
def empty_kafka_topic(request):
    """
    Deletes a kafka topic to clear message if it exists.
    """
    topic_name = get_original_test_name(request).replace("[", "_").replace("]", "")
    client = kafka_admin.AdminClient({"bootstrap.servers": BOOTSTRAP_SERVERS})
    for _, future in client.delete_topics([topic_name]).items():
        try:
            future.result()
        except KafkaException:
            pass  # The topic likely already doesn't exist

    client = kafka_admin.AdminClient({"bootstrap.servers": BOOTSTRAP_SERVERS})
    for _, future in client.create_topics([kafka_admin.NewTopic(topic_name, 1, 1)]).items():
        try:
            future.result()
        except KafkaException:
            pass  # The topic likely already exists
    yield topic_name


@pytest.fixture
def dummy_tracer():
    patch()
    t = DummyTracer()
    # disable backoff because it makes these tests less reliable
    if not config._trace_writer_native:
        t._span_aggregator.writer._send_payload_with_backoff = t._span_aggregator.writer._send_payload
    yield t
    unpatch()


@pytest.fixture
def should_filter_empty_polls():
    yield True


@pytest.fixture
def tracer(should_filter_empty_polls):
    patch()
    if should_filter_empty_polls:
        ddtracer.configure(trace_processors=[KafkaConsumerPollFilter()])
    # disable backoff because it makes these tests less reliable
    if not config._trace_writer_native:
        previous_backoff = ddtracer._span_aggregator.writer._send_payload_with_backoff
        ddtracer._span_aggregator.writer._send_payload_with_backoff = ddtracer._span_aggregator.writer._send_payload
    try:
        yield ddtracer
    finally:
        ddtracer.flush()
        if not config._trace_writer_native:
            ddtracer._span_aggregator.writer._send_payload_with_backoff = previous_backoff
        unpatch()


@pytest.fixture
def producer(tracer):
    _producer = confluent_kafka.Producer({"bootstrap.servers": BOOTSTRAP_SERVERS})
    Pin._override(_producer, tracer=tracer)
    return _producer


@pytest.fixture
def consumer(tracer, kafka_topic):
    _consumer = confluent_kafka.Consumer(
        {
            "bootstrap.servers": BOOTSTRAP_SERVERS,
            "group.id": GROUP_ID,
            "auto.offset.reset": "earliest",
        }
    )

    tp = TopicPartition(kafka_topic, 0)
    tp.offset = 0  # we want to read the first message
    _consumer.commit(offsets=[tp])
    Pin._override(_consumer, tracer=tracer)
    _consumer.subscribe([kafka_topic])
    yield _consumer
    _consumer.close()


@pytest.fixture
def non_auto_commit_consumer(tracer, kafka_topic):
    _consumer = confluent_kafka.Consumer(
        {
            "bootstrap.servers": BOOTSTRAP_SERVERS,
            "group.id": GROUP_ID,
            "auto.offset.reset": "earliest",
            "enable.auto.commit": False,
        }
    )
    tp = TopicPartition(kafka_topic, 0)
    tp.offset = 0  # we want to read the first message
    _consumer.commit(offsets=[tp])
    Pin._override(_consumer, tracer=tracer)
    _consumer.subscribe([kafka_topic])
    yield _consumer
    _consumer.close()


@pytest.fixture
def serializing_producer(tracer):
    _producer = confluent_kafka.SerializingProducer(
        {"bootstrap.servers": BOOTSTRAP_SERVERS, "value.serializer": lambda x, y: x}
    )
    Pin._override(_producer, tracer=tracer)
    return _producer


@pytest.fixture
def deserializing_consumer(tracer, kafka_topic):
    _consumer = confluent_kafka.DeserializingConsumer(
        {
            "bootstrap.servers": BOOTSTRAP_SERVERS,
            "group.id": GROUP_ID,
            "auto.offset.reset": "earliest",
            "value.deserializer": lambda x, y: x,
        }
    )
    Pin._override(_consumer, tracer=tracer)
    _consumer.subscribe([kafka_topic])
    yield _consumer
    _consumer.close()
