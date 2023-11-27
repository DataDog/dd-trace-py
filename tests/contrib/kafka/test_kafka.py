import logging
import os
import time

import confluent_kafka
from confluent_kafka import KafkaException
from confluent_kafka import TopicPartition
from confluent_kafka import admin as kafka_admin
import mock
import pytest

from ddtrace import Pin
from ddtrace import Tracer
from ddtrace.contrib.kafka.patch import patch
from ddtrace.contrib.kafka.patch import unpatch
from ddtrace.filters import TraceFilter
import ddtrace.internal.datastreams  # noqa: F401 - used as part of mock patching
from ddtrace.internal.datastreams.kafka import PROPAGATION_KEY
from ddtrace.internal.datastreams.processor import ConsumerPartitionKey
from ddtrace.internal.datastreams.processor import PartitionKey
from ddtrace.internal.utils.retry import fibonacci_backoff_with_jitter
from tests.contrib.config import KAFKA_CONFIG
from tests.utils import DummyTracer
from tests.utils import flaky
from tests.utils import override_config


GROUP_ID = "test_group"
BOOTSTRAP_SERVERS = "localhost:{}".format(KAFKA_CONFIG["port"])
KEY = "test_key"
PAYLOAD = bytes("hueh hueh hueh", encoding="utf-8")
DSM_TEST_PATH_HEADER_SIZE = 20


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


@pytest.fixture()
def empty_kafka_topic(request):
    """
    Deletes a kafka topic to clear message if it exists.
    """
    topic_name = request.node.name.replace("[", "_").replace("]", "")
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
def dsm_processor(tracer):
    processor = tracer.data_streams_processor
    with mock.patch("ddtrace.internal.datastreams.data_streams_processor", return_value=processor):
        yield processor


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

    tp = TopicPartition(kafka_topic, 0)
    tp.offset = 0  # we want to read the first message
    _consumer.commit(offsets=[tp])
    Pin.override(_consumer, tracer=tracer)
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
    Pin.override(_consumer, tracer=tracer)
    _consumer.subscribe([kafka_topic])
    yield _consumer
    _consumer.close()


@pytest.fixture
def serializing_producer(tracer):
    _producer = confluent_kafka.SerializingProducer(
        {"bootstrap.servers": BOOTSTRAP_SERVERS, "value.serializer": lambda x, y: x}
    )
    Pin.override(_producer, tracer=tracer)
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


def test_produce_none_key(dummy_tracer, producer, kafka_topic):
    Pin.override(producer, tracer=dummy_tracer)
    producer.produce(kafka_topic, PAYLOAD, key=None)
    producer.flush()

    traces = dummy_tracer.pop_traces()
    assert 1 == len(traces), "key=None does not cause produce() call to raise an exception"


def test_produce_multiple_servers(dummy_tracer, kafka_topic):
    producer = confluent_kafka.Producer({"bootstrap.servers": ",".join([BOOTSTRAP_SERVERS] * 3)})
    Pin.override(producer, tracer=dummy_tracer)
    producer.produce(kafka_topic, PAYLOAD, key=KEY)
    producer.flush()

    traces = dummy_tracer.pop_traces()
    assert 1 == len(traces)
    produce_span = traces[0][0]
    assert produce_span.get_tag("messaging.kafka.bootstrap.servers") == ",".join([BOOTSTRAP_SERVERS] * 3)


@flaky(until=1704067200)
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


@pytest.mark.snapshot(ignores=["metrics.kafka.message_offset"])
def test_commit(producer, consumer, kafka_topic):
    producer.produce(kafka_topic, PAYLOAD, key=KEY)
    producer.flush()
    message = None
    while message is None:
        message = consumer.poll(1.0)
    consumer.commit(message)


@pytest.mark.snapshot(ignores=["metrics.kafka.message_offset"])
def test_commit_with_offset(producer, consumer, kafka_topic):
    producer.produce(kafka_topic, PAYLOAD, key=KEY)
    producer.flush()
    message = None
    while message is None:
        message = consumer.poll(1.0)
    consumer.commit(offsets=[TopicPartition(kafka_topic)])


@pytest.mark.snapshot(ignores=["metrics.kafka.message_offset"])
def test_commit_with_only_async_arg(producer, consumer, kafka_topic):
    producer.produce(kafka_topic, PAYLOAD, key=KEY)
    producer.flush()
    message = None
    while message is None:
        message = consumer.poll(1.0)
    consumer.commit(asynchronous=False)


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
    for _ in range(10):
        x = factory()
        if x is not None:
            return x
        time.sleep(0.1)
    return None


@pytest.mark.parametrize("payload_and_length", [("test", 4), ("你".encode("utf-8"), 3), (b"test2", 5)])
@pytest.mark.parametrize("key_and_length", [("test-key", 8), ("你".encode("utf-8"), 3), (b"t2", 2)])
def test_data_streams_payload_size(dsm_processor, consumer, producer, kafka_topic, payload_and_length, key_and_length):
    payload, payload_length = payload_and_length
    key, key_length = key_and_length
    test_headers = {"1234": "5678"}
    test_header_size = 0
    for k, v in test_headers.items():
        test_header_size += len(k) + len(v)
    expected_payload_size = float(payload_length + key_length)
    expected_payload_size += test_header_size  # to account for headers we add here
    expected_payload_size += len(PROPAGATION_KEY)  # Add in header key length
    expected_payload_size += DSM_TEST_PATH_HEADER_SIZE  # to account for path header we add

    try:
        del dsm_processor._current_context.value
    except AttributeError:
        pass

    producer.produce(kafka_topic, payload, key=key, headers=test_headers)
    producer.flush()
    message = None
    while message is None:
        message = consumer.poll(1.0)
    buckets = dsm_processor._buckets
    assert len(buckets) == 1
    first = list(buckets.values())[0].pathway_stats
    for _bucket_name, bucket in first.items():
        assert bucket.payload_size._count >= 1
        assert bucket.payload_size._sum == expected_payload_size


def test_data_streams_kafka_serializing(dsm_processor, deserializing_consumer, serializing_producer, kafka_topic):
    PAYLOAD = bytes("data streams", encoding="utf-8")
    try:
        del dsm_processor._current_context.value
    except AttributeError:
        pass
    serializing_producer.produce(kafka_topic, value=PAYLOAD, key="test_key_2")
    serializing_producer.flush()
    message = None
    while message is None or str(message.value()) != str(PAYLOAD):
        message = deserializing_consumer.poll(1.0)
    buckets = dsm_processor._buckets
    assert len(buckets) == 1


def test_data_streams_kafka(dsm_processor, consumer, producer, kafka_topic):
    PAYLOAD = bytes("data streams", encoding="utf-8")
    try:
        del dsm_processor._current_context.value
    except AttributeError:
        pass
    producer.produce(kafka_topic, PAYLOAD, key="test_key_1")
    producer.produce(kafka_topic, PAYLOAD, key="test_key_2")
    producer.flush()
    message = None
    while message is None or str(message.value()) != str(PAYLOAD):
        message = consumer.poll(1.0)
    buckets = dsm_processor._buckets
    assert len(buckets) == 1
    first = list(buckets.values())[0].pathway_stats
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
    import ddtrace
    from ddtrace.contrib.kafka.patch import patch
    from ddtrace.contrib.kafka.patch import unpatch
    from ddtrace.filters import TraceFilter

    PAYLOAD = bytes("hueh hueh hueh", encoding="utf-8")

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


def test_data_streams_kafka_offset_monitoring_messages(dsm_processor, non_auto_commit_consumer, producer, kafka_topic):
    def _read_single_message(consumer):
        message = None
        while message is None or str(message.value()) != str(PAYLOAD):
            message = consumer.poll(1.0)
            if message:
                consumer.commit(asynchronous=False, message=message)
                return message

    PAYLOAD = bytes("data streams", encoding="utf-8")
    consumer = non_auto_commit_consumer
    try:
        del dsm_processor._current_context.value
    except AttributeError:
        pass
    buckets = dsm_processor._buckets
    producer.produce(kafka_topic, PAYLOAD, key="test_key_1")
    producer.produce(kafka_topic, PAYLOAD, key="test_key_2")
    producer.flush()

    _message = _read_single_message(consumer)  # noqa: F841

    assert len(buckets) == 1
    assert list(buckets.values())[0].latest_produce_offsets[PartitionKey(kafka_topic, 0)] > 0
    assert consumer.committed([TopicPartition(kafka_topic, 0)])[0].offset == 1
    assert list(buckets.values())[0].latest_commit_offsets[ConsumerPartitionKey("test_group", kafka_topic, 0)] == 0

    _message = _read_single_message(consumer)  # noqa: F841
    assert consumer.committed([TopicPartition(kafka_topic, 0)])[0].offset == 2
    assert list(buckets.values())[0].latest_commit_offsets[ConsumerPartitionKey("test_group", kafka_topic, 0)] == 1


def test_data_streams_kafka_offset_monitoring_offsets(dsm_processor, non_auto_commit_consumer, producer, kafka_topic):
    def _read_single_message(consumer):
        message = None
        while message is None or str(message.value()) != str(PAYLOAD):
            message = consumer.poll(1.0)
            if message and message.offset() is not None:
                tp = TopicPartition(message.topic(), message.partition())
                tp.offset = message.offset() + 1
                offsets = [tp]

                consumer.commit(asynchronous=False, offsets=offsets)
                return message

    consumer = non_auto_commit_consumer
    PAYLOAD = bytes("data streams", encoding="utf-8")
    try:
        del dsm_processor._current_context.value
    except AttributeError:
        pass
    producer.produce(kafka_topic, PAYLOAD, key="test_key_1")
    producer.produce(kafka_topic, PAYLOAD, key="test_key_2")
    producer.flush()

    _message = _read_single_message(consumer)  # noqa: F841

    buckets = dsm_processor._buckets
    assert len(buckets) == 1
    assert list(buckets.values())[0].latest_produce_offsets[PartitionKey(kafka_topic, 0)] > 0
    assert consumer.committed([TopicPartition(kafka_topic, 0)])[0].offset == 1
    assert list(buckets.values())[0].latest_commit_offsets[ConsumerPartitionKey("test_group", kafka_topic, 0)] == 0

    _message = _read_single_message(consumer)  # noqa: F841
    assert consumer.committed([TopicPartition(kafka_topic, 0)])[0].offset == 2
    assert list(buckets.values())[0].latest_commit_offsets[ConsumerPartitionKey("test_group", kafka_topic, 0)] == 1


def test_data_streams_kafka_offset_monitoring_auto_commit(dsm_processor, consumer, producer, kafka_topic):
    def _read_single_message(consumer):
        message = None
        while message is None or str(message.value()) != str(PAYLOAD):
            message = consumer.poll(1.0)
            if message:
                return message

    PAYLOAD = bytes("data streams", encoding="utf-8")
    try:
        del dsm_processor._current_context.value
    except AttributeError:
        pass
    producer.produce(kafka_topic, PAYLOAD, key="test_key_1")
    producer.produce(kafka_topic, PAYLOAD, key="test_key_2")
    producer.flush()

    _message = _read_single_message(consumer)  # noqa: F841
    consumer.commit(asynchronous=False)

    buckets = dsm_processor._buckets
    assert len(buckets) == 1
    assert list(buckets.values())[0].latest_produce_offsets[PartitionKey(kafka_topic, 0)] > 0
    assert consumer.committed([TopicPartition(kafka_topic, 0)])[0].offset == 1
    assert list(buckets.values())[0].latest_commit_offsets[ConsumerPartitionKey("test_group", kafka_topic, 0)] == 0

    _message = _read_single_message(consumer)  # noqa: F841
    consumer.commit(asynchronous=False)
    assert consumer.committed([TopicPartition(kafka_topic, 0)])[0].offset == 2
    assert list(buckets.values())[0].latest_commit_offsets[ConsumerPartitionKey("test_group", kafka_topic, 0)] == 1


def test_data_streams_kafka_produce_api_compatibility(dsm_processor, consumer, producer, empty_kafka_topic):
    kafka_topic = empty_kafka_topic

    def _read_single_message(consumer):
        message = None
        while message is None or str(message.value()) != str(PAYLOAD):
            message = consumer.poll(1.0)
            if message:
                return message

    PAYLOAD = bytes("data streams", encoding="utf-8")
    try:
        del dsm_processor._current_context.value
    except AttributeError:
        pass

    # All of these should work
    producer.produce(kafka_topic)
    producer.produce(kafka_topic, PAYLOAD)
    producer.produce(kafka_topic, value=PAYLOAD)
    producer.produce(kafka_topic, PAYLOAD, key="test_key_1")
    producer.produce(kafka_topic, value=PAYLOAD, key="test_key_2")
    producer.produce(kafka_topic, key="test_key_3")
    producer.flush()

    buckets = dsm_processor._buckets
    assert len(buckets) == 1
    assert list(buckets.values())[0].latest_produce_offsets[PartitionKey(kafka_topic, 0)] == 5


def test_data_streams_default_context_propagation(dummy_tracer, consumer, producer, kafka_topic):
    Pin.override(producer, tracer=dummy_tracer)
    Pin.override(consumer, tracer=dummy_tracer)

    test_string = "context test"
    PAYLOAD = bytes(test_string, encoding="utf-8")

    producer.produce(kafka_topic, PAYLOAD, key="test_key")
    producer.flush()

    message = None
    while message is None or str(message.value()) != str(PAYLOAD):
        message = consumer.poll(1.0)

    # message comes back with expected test string
    assert message.value() == b"context test"

    # DSM header 'dd-pathway-ctx' was propagated in the headers
    assert message.headers()[0][0] == PROPAGATION_KEY
    assert message.headers()[0][1] is not None


# It is not currently expected for kafka produce and consume spans to connect in a trace
def test_tracing_context_is_not_propagated(dummy_tracer, consumer, producer, kafka_topic):
    Pin.override(producer, tracer=dummy_tracer)
    Pin.override(consumer, tracer=dummy_tracer)

    test_string = "context test"
    PAYLOAD = bytes(test_string, encoding="utf-8")

    producer.produce(kafka_topic, PAYLOAD, key="test_key")
    producer.flush()

    message = None
    while message is None or str(message.value()) != str(PAYLOAD):
        message = consumer.poll(1.0)

    # message comes back with expected test string
    assert message.value() == b"context test"

    traces = dummy_tracer.pop_traces()
    produce_span = traces[0][0]
    consume_span1 = traces[1][0]
    consume_span2 = traces[2][0]

    # kafka.produce span is created without a parent
    assert produce_span.name == "kafka.produce"
    assert produce_span.parent_id is None
    assert produce_span.get_tag("pathway.hash") is not None

    # None of the kafka.consume spans have parents
    assert consume_span1.name == "kafka.consume"
    assert consume_span1.parent_id is None
    assert consume_span2.name == "kafka.consume"
    assert consume_span2.parent_id is None

    # None of these spans are part of the same trace
    assert produce_span.trace_id != consume_span1.trace_id
    assert consume_span1.trace_id != consume_span2.trace_id


def test_span_has_dsm_payload_hash(dummy_tracer, consumer, producer, kafka_topic):
    Pin.override(producer, tracer=dummy_tracer)
    Pin.override(consumer, tracer=dummy_tracer)

    test_string = "payload hash test"
    PAYLOAD = bytes(test_string, encoding="utf-8")

    producer.produce(kafka_topic, PAYLOAD, key="test_payload_hash_key")
    producer.flush()

    message = None
    while message is None or str(message.value()) != str(PAYLOAD):
        message = consumer.poll(1.0)

    # message comes back with expected test string
    assert message.value() == b"payload hash test"

    traces = dummy_tracer.pop_traces()
    produce_span = traces[0][0]
    consume_span = traces[len(traces) - 1][0]

    # kafka.produce and kafka.consume span have payload hash
    assert produce_span.name == "kafka.produce"
    assert produce_span.get_tag("pathway.hash") is not None

    assert consume_span.name == "kafka.consume"
    assert consume_span.get_tag("pathway.hash") is not None
