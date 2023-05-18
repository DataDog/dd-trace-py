import logging

import confluent_kafka
import pytest
import six

from ddtrace import Pin
from ddtrace import tracer as dd_tracer
from ddtrace.contrib.kafka.patch import patch
from ddtrace.contrib.kafka.patch import unpatch
from ddtrace.filters import TraceFilter
from tests.contrib.config import KAFKA_CONFIG
from tests.utils import override_config


TOPIC_NAME = "test_topic"
GROUP_ID = "test_group"
BOOTSTRAP_SERVERS = "localhost:{}".format(KAFKA_CONFIG["port"])
KEY = "test_key"
if six.PY3:
    PAYLOAD = bytes("hueh hueh hueh", encoding="utf-8")
else:
    PAYLOAD = bytes("hueh hueh hueh")


class KafkaConsumerPollFilter(TraceFilter):
    def process_trace(self, trace):
        # Filter out all poll spans that have no received message
        return (
            None
            if trace[0].name == "kafka.consume" and trace[0].get_tag("kafka.received_message") == "False"
            else trace
        )


dd_tracer.configure(settings={"FILTERS": [KafkaConsumerPollFilter()]})


@pytest.fixture
def tracer():
    patch()
    yield dd_tracer
    unpatch()


@pytest.fixture
def producer(tracer):
    _producer = confluent_kafka.Producer({"bootstrap.servers": BOOTSTRAP_SERVERS})
    Pin.override(_producer, tracer=tracer)
    return _producer


@pytest.fixture
def consumer(tracer):
    _consumer = confluent_kafka.Consumer(
        {
            "bootstrap.servers": BOOTSTRAP_SERVERS,
            "group.id": GROUP_ID,
            "auto.offset.reset": "earliest",
        }
    )
    Pin.override(_consumer, tracer=tracer)
    _consumer.subscribe([TOPIC_NAME])
    yield _consumer
    _consumer.close()


def test_consumer_created_with_logger_does_not_raise(tracer):
    """Test that adding a logger to a Consumer init does not raise any errors."""
    logger = logging.getLogger()
    # regression test for DataDog/dd-trace-py/issues/5873
    consumer = confluent_kafka.Consumer(
        {
            "bootstrap.servers": BOOTSTRAP_SERVERS,
            "group.id": GROUP_ID,
            "auto.offset.reset": "earliest",
        },
        logger=logger,
    )
    consumer.close()


@pytest.mark.parametrize("tombstone", [False, True])
@pytest.mark.snapshot(ignores=["metrics.kafka.message_offset"])
def test_message(producer, consumer, tombstone):
    if tombstone:
        producer.produce(TOPIC_NAME, key=KEY)
    else:
        producer.produce(TOPIC_NAME, PAYLOAD, key=KEY)
    producer.flush()
    message = None
    while message is None:
        message = consumer.poll(1.0)


@pytest.mark.snapshot(
    token="tests.contrib.kafka.test_kafka.test_service_override", ignores=["metrics.kafka.message_offset"]
)
def test_service_override_config(producer, consumer):
    with override_config("kafka", dict(service="my-custom-service-name")):
        producer.produce(TOPIC_NAME, PAYLOAD, key=KEY)
        producer.flush()
        message = None
        while message is None:
            message = consumer.poll(1.0)


@pytest.mark.subprocess(env=dict(DD_KAFKA_SERVICE="my-custom-service-name"), err=None)
@pytest.mark.snapshot(
    token="tests.contrib.kafka.test_kafka.test_service_override", ignores=["metrics.kafka.message_offset"]
)
def test_service_override_env_var():
    import six

    import ddtrace
    from ddtrace.contrib.kafka.patch import patch
    from ddtrace.contrib.kafka.patch import unpatch
    from ddtrace.filters import TraceFilter

    PAYLOAD = bytes("hueh hueh hueh", encoding="utf-8") if six.PY3 else bytes("hueh hueh hueh")

    class KafkaConsumerPollFilter(TraceFilter):
        def process_trace(self, trace):
            # Filter out all poll spans that have no received message
            return (
                None
                if trace[0].name == "kafka.consume" and trace[0].get_tag("kafka.received_message") == "False"
                else trace
            )

    ddtrace.tracer.configure(settings={"FILTERS": [KafkaConsumerPollFilter()]})
    patch()
    import confluent_kafka

    producer = confluent_kafka.Producer({"bootstrap.servers": "localhost:29092"})
    consumer = confluent_kafka.Consumer(
        {
            "bootstrap.servers": "localhost:29092",
            "group.id": "test_group",
            "auto.offset.reset": "earliest",
        }
    )
    ddtrace.Pin.override(producer, tracer=ddtrace.tracer)
    ddtrace.Pin.override(consumer, tracer=ddtrace.tracer)
    consumer.subscribe(["test_topic"])

    producer.produce("test_topic", PAYLOAD, key="test_key")
    producer.flush()
    message = None
    while message is None:
        message = consumer.poll(1.0)
    unpatch()
    consumer.close()


@pytest.mark.snapshot(ignores=["metrics.kafka.message_offset"])
def test_analytics_with_rate(producer, consumer):
    with override_config("kafka", dict(analytics_enabled=True, analytics_sample_rate=0.5)):
        producer.produce(TOPIC_NAME, PAYLOAD, key=KEY)
        producer.flush()
        message = None
        while message is None:
            message = consumer.poll(1.0)


@pytest.mark.snapshot(ignores=["metrics.kafka.message_offset"])
def test_analytics_without_rate(producer, consumer):
    with override_config("kafka", dict(analytics_enabled=True)):
        producer.produce(TOPIC_NAME, PAYLOAD, key=KEY)
        producer.flush()
        message = None
        while message is None:
            message = consumer.poll(1.0)
