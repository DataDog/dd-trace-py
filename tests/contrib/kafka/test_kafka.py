import logging
import os
import time

import confluent_kafka
from confluent_kafka import KafkaException
from confluent_kafka import admin as kafka_admin
import pytest
import six

from ddtrace import Pin
from ddtrace import Tracer
from ddtrace.contrib.kafka.patch import patch
from ddtrace.contrib.kafka.patch import unpatch
from ddtrace.filters import TraceFilter
from ddtrace.internal.utils.retry import fibonacci_backoff_with_jitter
from tests.contrib.config import KAFKA_CONFIG
from tests.utils import DummyTracer
from tests.utils import override_config


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
        if trace[0].name == "kafka.consume" and trace[0].get_tag("kafka.received_message") == "False":
            return None

        return trace


@pytest.fixture()
def kafka_topic(request):
    topic_name = request.node.name.replace("[", "_").replace("]", "")

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
    yield DummyTracer()
    unpatch()


@pytest.fixture
def tracer():
    patch()
    t = Tracer()
    t.configure(settings={"FILTERS": [KafkaConsumerPollFilter()]})
    try:
        yield t
    finally:
        t.flush()
        t.shutdown()
        unpatch()


@pytest.fixture
def producer(tracer):
    _producer = confluent_kafka.Producer({"bootstrap.servers": BOOTSTRAP_SERVERS})
    Pin.override(_producer, tracer=tracer)
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
    Pin.override(_consumer, tracer=tracer)
    _consumer.subscribe([kafka_topic])
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


@pytest.mark.parametrize(
    "config,expect_servers",
    [
        ({"bootstrap.servers": BOOTSTRAP_SERVERS}, BOOTSTRAP_SERVERS),
        ({"metadata.broker.list": BOOTSTRAP_SERVERS}, BOOTSTRAP_SERVERS),
        ({}, None),
    ],
)
def test_producer_bootstrap_servers(config, expect_servers, tracer):
    producer = confluent_kafka.Producer(config)
    if expect_servers is not None:
        assert producer._dd_bootstrap_servers == expect_servers
    else:
        assert producer._dd_bootstrap_servers is None


def test_produce_single_server(dummy_tracer, producer, kafka_topic):
    Pin.override(producer, tracer=dummy_tracer)
    producer.produce(kafka_topic, PAYLOAD, key=KEY)
    producer.flush()

    traces = dummy_tracer.pop_traces()
    assert 1 == len(traces)
    produce_span = traces[0][0]
    assert produce_span.get_tag("messaging.kafka.bootstrap.servers") == BOOTSTRAP_SERVERS


def test_produce_multiple_servers(dummy_tracer, kafka_topic):
    producer = confluent_kafka.Producer({"bootstrap.servers": ",".join([BOOTSTRAP_SERVERS] * 3)})
    Pin.override(producer, tracer=dummy_tracer)
    producer.produce(kafka_topic, PAYLOAD, key=KEY)
    producer.flush()

    traces = dummy_tracer.pop_traces()
    assert 1 == len(traces)
    produce_span = traces[0][0]
    assert produce_span.get_tag("messaging.kafka.bootstrap.servers") == ",".join([BOOTSTRAP_SERVERS] * 3)


@pytest.mark.parametrize("tombstone", [False, True])
@pytest.mark.snapshot(ignores=["metrics.kafka.message_offset"])
def test_message(producer, consumer, tombstone, kafka_topic):
    if tombstone:
        producer.produce(kafka_topic, key=KEY)
    else:
        producer.produce(kafka_topic, PAYLOAD, key=KEY)
    producer.flush()
    message = None
    while message is None:
        message = consumer.poll(1.0)


@pytest.mark.snapshot(
    token="tests.contrib.kafka.test_kafka.test_service_override", ignores=["metrics.kafka.message_offset"]
)
def test_service_override_config(producer, consumer, kafka_topic):
    with override_config("kafka", dict(service="my-custom-service-name")):
        producer.produce(kafka_topic, PAYLOAD, key=KEY)
        producer.flush()
        message = None
        while message is None:
            message = consumer.poll(1.0)


@pytest.mark.snapshot(ignores=["metrics.kafka.message_offset"])
def test_analytics_with_rate(producer, consumer, kafka_topic):
    with override_config("kafka", dict(analytics_enabled=True, analytics_sample_rate=0.5)):
        producer.produce(kafka_topic, PAYLOAD, key=KEY)
        producer.flush()
        message = None
        while message is None:
            message = consumer.poll(1.0)


@pytest.mark.snapshot(ignores=["metrics.kafka.message_offset"])
def test_analytics_without_rate(producer, consumer, kafka_topic):
    with override_config("kafka", dict(analytics_enabled=True)):
        producer.produce(kafka_topic, PAYLOAD, key=KEY)
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


def test_data_streams_kafka(tracer, consumer, producer, kafka_topic):
    PAYLOAD = bytes("data streams", encoding="utf-8") if six.PY3 else bytes("data streams")
    try:
        del tracer.data_streams_processor._current_context.value
    except AttributeError:
        pass
    producer.produce(kafka_topic, PAYLOAD, key="test_key_2")
    producer.flush()
    message = None
    while message is None or str(message.value()) != str(PAYLOAD):
        message = consumer.poll(1.0)
    buckets = tracer.data_streams_processor._buckets
    assert len(buckets) == 1
    _, first = list(buckets.items())[0]
    assert (
        first[
            ("direction:out,topic:{},type:kafka".format(kafka_topic), 7591515074392955298, 0)
        ].full_pathway_latency._count
        >= 1
    )
    assert (
        first[("direction:out,topic:{},type:kafka".format(kafka_topic), 7591515074392955298, 0)].edge_latency._count
        >= 1
    )
    assert (
        first[
            (
                "direction:in,group:test_group,topic:{},type:kafka".format(kafka_topic),
                6611771803293368236,
                7591515074392955298,
            )
        ].full_pathway_latency._count
        >= 1
    )
    assert (
        first[
            (
                "direction:in,group:test_group,topic:{},type:kafka".format(kafka_topic),
                6611771803293368236,
                7591515074392955298,
            )
        ].edge_latency._count
        >= 1
    )


def _generate_in_subprocess(random_topic):
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

    # We run all of these commands with retry attempts because the kafka-confluent API
    # sys.exits on connection failures, which causes the test to fail. We want to retry
    # until the connection is established. Connection failures are somewhat common.
    fibonacci_backoff_with_jitter(5)(consumer.subscribe)([random_topic])
    fibonacci_backoff_with_jitter(5)(producer.produce)(random_topic, PAYLOAD, key="test_key")
    fibonacci_backoff_with_jitter(5, until=lambda result: isinstance(result, int))(producer.flush)()
    message = None
    while message is None:
        message = fibonacci_backoff_with_jitter(5, until=lambda result: not isinstance(result, Exception))(
            consumer.poll
        )(1.0)

    unpatch()
    consumer.close()


@pytest.mark.snapshot(
    token="tests.contrib.kafka.test_kafka.test_service_override_env_var", ignores=["metrics.kafka.message_offset"]
)
def test_service_override_env_var(ddtrace_run_python_code_in_subprocess, kafka_topic):
    code = """
import sys
import pytest
from tests.contrib.kafka.test_kafka import _generate_in_subprocess
from tests.contrib.kafka.test_kafka import kafka_topic


def test():
    _generate_in_subprocess("{}")

if __name__ == "__main__":
    sys.exit(pytest.main(["-x", __file__]))
    """.format(
        kafka_topic
    )
    env = os.environ.copy()
    env["DD_KAFKA_SERVICE"] = "my-custom-service-name"
    out, err, status, _ = ddtrace_run_python_code_in_subprocess(code, env=env)
    assert status == 0, out.decode()
    assert err == b"", err.decode()


@pytest.mark.snapshot(ignores=["metrics.kafka.message_offset"])
@pytest.mark.parametrize("service", [None, "mysvc"])
@pytest.mark.parametrize("schema", [None, "v0", "v1"])
def test_schematized_span_service_and_operation(ddtrace_run_python_code_in_subprocess, service, schema, kafka_topic):
    code = """
import sys
import pytest
from tests.contrib.kafka.test_kafka import _generate_in_subprocess

def test():
    _generate_in_subprocess("{}")

if __name__ == "__main__":
    sys.exit(pytest.main(["-x", __file__]))
    """.format(
        kafka_topic
    )
    env = os.environ.copy()
    if service:
        env["DD_SERVICE"] = service
    if schema:
        env["DD_TRACE_SPAN_ATTRIBUTE_SCHEMA"] = schema
    out, err, status, _ = ddtrace_run_python_code_in_subprocess(code, env=env)
    assert status == 0, out.decode()
    assert err == b"", err.decode()
