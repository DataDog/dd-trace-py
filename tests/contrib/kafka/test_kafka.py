import logging
import os
import time

import confluent_kafka
import pytest
import six
from tenacity import Retrying
from tenacity import stop_after_attempt
from tenacity import wait_random

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
            if trace[0].name in {"kafka.consume", "kafka.process"}
            and trace[0].get_tag("kafka.received_message") == "False"
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


def retry_until_not_none(factory):
    for i in range(10):
        x = factory()
        if x is not None:
            return x
        time.sleep(0.1)
    return None


def test_data_streams_kafka(consumer, producer):
    PAYLOAD = bytes("data streams", encoding="utf-8") if six.PY3 else bytes("data streams")
    try:
        del dd_tracer.data_streams_processor._current_context.value
    except AttributeError:
        pass
    producer.produce(TOPIC_NAME, PAYLOAD, key="test_key_2")
    producer.flush()
    message = None
    while message is None or str(message.value()) != str(PAYLOAD):
        message = consumer.poll(1.0)
    buckets = dd_tracer.data_streams_processor._buckets
    assert len(buckets) == 1
    _, first = list(buckets.items())[0]
    assert first[("direction:out,topic:test_topic,type:kafka", 7591950451013596431, 0)].full_pathway_latency._count >= 1
    assert first[("direction:out,topic:test_topic,type:kafka", 7591950451013596431, 0)].edge_latency._count >= 1
    assert (
        first[
            ("direction:in,group:test_group,topic:test_topic,type:kafka", 17357311454188123272, 7591950451013596431)
        ].full_pathway_latency._count
        >= 1
    )
    assert (
        first[
            ("direction:in,group:test_group,topic:test_topic,type:kafka", 17357311454188123272, 7591950451013596431)
        ].edge_latency._count
        >= 1
    )


def _generate_in_subprocess(topic):
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
                if trace[0].name in {"kafka.consume", "kafka.process"}
                and trace[0].get_tag("kafka.received_message") == "False"
                else trace
            )

    ddtrace.tracer.configure(settings={"FILTERS": [KafkaConsumerPollFilter()]})
    patch()

    producer = None
    consumer = None
    published = False
    subscribed = False
    # Retry the Kafka commands several times, as Kafka may return connection refused
    for attempt in Retrying(stop=stop_after_attempt(5), wait=wait_random(min=1, max=2)):
        with attempt:
            producer = producer or confluent_kafka.Producer({"bootstrap.servers": "localhost:29092"})
            consumer = confluent_kafka.Consumer(
                {
                    "bootstrap.servers": "localhost:29092",
                    "group.id": "test_group",
                    "auto.offset.reset": "earliest",
                }
            )
            ddtrace.Pin.override(producer, tracer=ddtrace.tracer)
            ddtrace.Pin.override(consumer, tracer=ddtrace.tracer)
            if not subscribed:
                consumer.subscribe([topic])
                subscribed = True
            if not published:
                producer.produce(topic, PAYLOAD, key="test_key")
                producer.flush()
                published = True
            message = None
            while message is None:
                message = consumer.poll(1.0)
            unpatch()
            consumer.close()


@pytest.mark.snapshot(
    token="tests.contrib.kafka.test_kafka.test_service_override", ignores=["metrics.kafka.message_offset"]
)
@pytest.mark.flaky(retries=5)  # The kafka-confluent API encounters segfaults occasionally
def test_service_override_env_var(ddtrace_run_python_code_in_subprocess):
    code = """
import sys
import pytest
from tests.contrib.kafka.test_kafka import _generate_in_subprocess

def test():
    _generate_in_subprocess('test_topic')

if __name__ == "__main__":
    sys.exit(pytest.main(["-x", __file__]))
    """
    env = os.environ.copy()
    env["DD_KAFKA_SERVICE"] = "my-custom-service-name"
    out, err, status, _ = ddtrace_run_python_code_in_subprocess(code, env=env)
    assert status == 0, out.decode()
    assert err == b"", err.decode()


@pytest.mark.snapshot(ignores=["metrics.kafka.message_offset"])
@pytest.mark.flaky(retries=5)  # The kafka-confluent API encounters segfaults occasionally
@pytest.mark.parametrize("service", [None, "mysvc"])
@pytest.mark.parametrize("schema", [None, "v0", "v1"])
def test_schematized_span_service_and_operation(ddtrace_run_python_code_in_subprocess, service, schema):
    code = """
import sys
import pytest
from tests.contrib.kafka.test_kafka import _generate_in_subprocess

def test():
    _generate_in_subprocess('test-schema.{}.{}')

if __name__ == "__main__":
    sys.exit(pytest.main(["-x", __file__]))
    """.format(
        service, schema
    )
    env = os.environ.copy()
    if service:
        env["DD_SERVICE"] = service
    if schema:
        env["DD_TRACE_SPAN_ATTRIBUTE_SCHEMA"] = schema
    out, err, status, _ = ddtrace_run_python_code_in_subprocess(code, env=env)
    assert status == 0, out.decode()
    assert err == b"", err.decode()
