import json
import logging
import os
import random
import time

import confluent_kafka
from confluent_kafka import KafkaException
from confluent_kafka import TopicPartition
from confluent_kafka import admin as kafka_admin
import mock
import pytest

from ddtrace import Pin
from ddtrace import Tracer
from ddtrace.contrib.kafka.patch import TracedConsumer
from ddtrace.contrib.kafka.patch import patch
from ddtrace.contrib.kafka.patch import unpatch
from ddtrace.filters import TraceFilter
import ddtrace.internal.datastreams  # noqa: F401 - used as part of mock patching
from ddtrace.internal.datastreams.processor import PROPAGATION_KEY_BASE_64
from ddtrace.internal.datastreams.processor import ConsumerPartitionKey
from ddtrace.internal.datastreams.processor import DataStreamsCtx
from ddtrace.internal.datastreams.processor import PartitionKey
from ddtrace.internal.utils.retry import fibonacci_backoff_with_jitter
from tests.contrib.config import KAFKA_CONFIG
from tests.datastreams.test_public_api import MockedTracer
from tests.utils import DummyTracer
from tests.utils import override_config


GROUP_ID = "test_group"
BOOTSTRAP_SERVERS = "localhost:{}".format(KAFKA_CONFIG["port"])
KEY = "test_key"
PAYLOAD = bytes("hueh hueh hueh", encoding="utf-8")
DSM_TEST_PATH_HEADER_SIZE = 28


class KafkaConsumerPollFilter(TraceFilter):
    def process_trace(self, trace):
        # Filter out all poll spans that have no received message
        if trace[0].name == "kafka.consume" and trace[0].get_tag("kafka.received_message") == "False":
            return None

        return trace


@pytest.fixture()
def kafka_topic(request):
    # todo: add a UUID, but it makes snapshot tests fail.
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
    t = DummyTracer()
    # disable backoff because it makes these tests less reliable
    t._writer._send_payload_with_backoff = t._writer._send_payload
    yield t
    unpatch()


@pytest.fixture
def should_filter_empty_polls():
    yield True


@pytest.fixture
def tracer(should_filter_empty_polls):
    patch()
    t = Tracer()
    if should_filter_empty_polls:
        t.configure(settings={"FILTERS": [KafkaConsumerPollFilter()]})
    # disable backoff because it makes these tests less reliable
    t._writer._send_payload_with_backoff = t._writer._send_payload
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


def test_empty_list_from_consume_does_not_raise():
    # https://github.com/DataDog/dd-trace-py/issues/8846
    patch()
    consumer = confluent_kafka.Consumer(
        {
            "bootstrap.servers": BOOTSTRAP_SERVERS,
            "group.id": GROUP_ID,
            "auto.offset.reset": "earliest",
            "request.timeout.ms": 1000,
            "retry.backoff.ms": 10,
        },
    )
    assert isinstance(consumer, TracedConsumer)
    max_messages_per_batch = 1
    timeout = 0
    consumer.consume(max_messages_per_batch, timeout)
    consumer.close()
    unpatch()


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
    Pin.override(producer, tracer=None)


def test_produce_multiple_servers(dummy_tracer, kafka_topic):
    producer = confluent_kafka.Producer({"bootstrap.servers": ",".join([BOOTSTRAP_SERVERS] * 3)})
    Pin.override(producer, tracer=dummy_tracer)
    producer.produce(kafka_topic, PAYLOAD, key=KEY)
    producer.flush()

    traces = dummy_tracer.pop_traces()
    assert 1 == len(traces)
    produce_span = traces[0][0]
    assert produce_span.get_tag("messaging.kafka.bootstrap.servers") == ",".join([BOOTSTRAP_SERVERS] * 3)
    Pin.override(producer, tracer=None)


@pytest.mark.parametrize("tombstone", [False, True])
@pytest.mark.snapshot(ignores=["metrics.kafka.message_offset"])
def test_message(producer, consumer, tombstone, kafka_topic):
    with override_config("kafka", dict(trace_empty_poll_enabled=False)):
        if tombstone:
            producer.produce(kafka_topic, key=KEY)
        else:
            producer.produce(kafka_topic, PAYLOAD, key=KEY)
        producer.flush()


@pytest.mark.snapshot(ignores=["metrics.kafka.message_offset"])
def test_commit(producer, consumer, kafka_topic):
    with override_config("kafka", dict(trace_empty_poll_enabled=False)):
        producer.produce(kafka_topic, PAYLOAD, key=KEY)
        producer.flush()
        message = consumer.poll()
        consumer.commit(message)


@pytest.mark.snapshot(ignores=["metrics.kafka.message_offset"])
def test_commit_with_consume_single_message(producer, consumer, kafka_topic):
    with override_config("kafka", dict(trace_empty_poll_enabled=False)):
        producer.produce(kafka_topic, PAYLOAD, key=KEY)
        producer.flush()
        # One message is consumed and one span is generated.
        messages = consumer.consume(num_messages=1)
        assert len(messages) == 1
        consumer.commit(messages[0])


@pytest.mark.snapshot(ignores=["metrics.kafka.message_offset"])
def test_commit_with_consume_with_multiple_messages(producer, consumer, kafka_topic):
    with override_config("kafka", dict(trace_empty_poll_enabled=False)):
        producer.produce(kafka_topic, PAYLOAD, key=KEY)
        producer.produce(kafka_topic, PAYLOAD, key=KEY)
        producer.flush()
        # Two messages are consumed but only ONE span is generated
        messages = consumer.consume(num_messages=2)
        assert len(messages) == 2


@pytest.mark.snapshot(ignores=["metrics.kafka.message_offset", "meta.error.stack"])
@pytest.mark.parametrize("should_filter_empty_polls", [False])
def test_commit_with_consume_with_error(producer, consumer, kafka_topic):
    producer.produce(kafka_topic, PAYLOAD, key=KEY)
    producer.flush()
    # Raises an exception by consuming messages after the consumer has been closed
    with pytest.raises(TypeError):
        # Empty poll spans are filtered out by the KafkaConsumerPollFilter. We need to disable
        # it to test error spans.
        # Allowing empty poll spans could introduce flakiness in the test.
        with override_config("kafka", dict(trace_empty_poll_enabled=True)):
            consumer.consume(num_messages=1, invalid_args="invalid_args")


@pytest.mark.snapshot(ignores=["metrics.kafka.message_offset"])
def test_commit_with_offset(producer, consumer, kafka_topic):
    with override_config("kafka", dict(trace_empty_poll_enabled=False)):
        producer.produce(kafka_topic, PAYLOAD, key=KEY)
        producer.flush()
        consumer.poll()
        consumer.commit(offsets=[TopicPartition(kafka_topic)])


@pytest.mark.snapshot(ignores=["metrics.kafka.message_offset"])
def test_commit_with_only_async_arg(producer, consumer, kafka_topic):
    with override_config("kafka", dict(trace_empty_poll_enabled=False)):
        producer.produce(kafka_topic, PAYLOAD, key=KEY)
        producer.flush()
        consumer.poll()
        consumer.commit(asynchronous=False)


@pytest.mark.snapshot(
    token="tests.contrib.kafka.test_kafka.test_service_override", ignores=["metrics.kafka.message_offset"]
)
def test_service_override_config(producer, consumer, kafka_topic):
    with override_config("kafka", dict(service="my-custom-service-name", trace_empty_poll_enabled=False)):
        producer.produce(kafka_topic, PAYLOAD, key=KEY)
        producer.flush()
        consumer.poll()


@pytest.mark.snapshot(ignores=["metrics.kafka.message_offset"])
def test_analytics_with_rate(producer, consumer, kafka_topic):
    with override_config(
        "kafka", dict(analytics_enabled=True, analytics_sample_rate=0.5, trace_empty_poll_enabled=False)
    ):
        producer.produce(kafka_topic, PAYLOAD, key=KEY)
        producer.flush()


@pytest.mark.snapshot(ignores=["metrics.kafka.message_offset"])
def test_analytics_without_rate(producer, consumer, kafka_topic):
    with override_config("kafka", dict(analytics_enabled=True, trace_empty_poll_enabled=False)):
        producer.produce(kafka_topic, PAYLOAD, key=KEY)
        producer.flush()


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
    expected_payload_size += len(PROPAGATION_KEY_BASE_64)  # Add in header key length
    expected_payload_size += DSM_TEST_PATH_HEADER_SIZE  # to account for path header we add

    try:
        del dsm_processor._current_context.value
    except AttributeError:
        pass

    producer.produce(kafka_topic, payload, key=key, headers=test_headers)
    producer.flush()
    consumer.poll()
    buckets = dsm_processor._buckets
    assert len(buckets) == 1
    first = list(buckets.values())[0].pathway_stats
    for _bucket_name, bucket in first.items():
        assert bucket.payload_size.count >= 1
        assert bucket.payload_size.sum == expected_payload_size


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
        message = deserializing_consumer.poll()
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
        message = consumer.poll()
    buckets = dsm_processor._buckets
    assert len(buckets) == 1
    first = list(buckets.values())[0].pathway_stats
    ctx = DataStreamsCtx(MockedTracer().data_streams_processor, 0, 0, 0)
    parent_hash = ctx._compute_hash(sorted(["direction:out", "type:kafka", "topic:{}".format(kafka_topic)]), 0)
    child_hash = ctx._compute_hash(
        sorted(["direction:in", "type:kafka", "group:test_group", "topic:{}".format(kafka_topic)]), parent_hash
    )
    assert (
        first[("direction:out,topic:{},type:kafka".format(kafka_topic), parent_hash, 0)].full_pathway_latency.count >= 1
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


def _generate_in_subprocess(random_topic):
    import ddtrace
    from ddtrace.contrib.kafka.patch import patch
    from ddtrace.contrib.kafka.patch import unpatch
    from tests.contrib.kafka.test_kafka import KafkaConsumerPollFilter

    PAYLOAD = bytes("hueh hueh hueh", encoding="utf-8")

    ddtrace.tracer.configure(settings={"FILTERS": [KafkaConsumerPollFilter()]})
    # disable backoff because it makes these tests less reliable
    ddtrace.tracer._writer._send_payload_with_backoff = ddtrace.tracer._writer._send_payload
    patch()

    producer = confluent_kafka.Producer({"bootstrap.servers": BOOTSTRAP_SERVERS})
    consumer = confluent_kafka.Consumer(
        {
            "bootstrap.servers": BOOTSTRAP_SERVERS,
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
    consumer.poll()

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
    env["DD_KAFKA_EMPTY_POLL_ENABLED"] = "False"
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
    env["DD_KAFKA_EMPTY_POLL_ENABLED"] = "False"
    out, err, status, _ = ddtrace_run_python_code_in_subprocess(code, env=env)
    assert status == 0, out.decode()
    assert err == b"", err.decode()


def test_data_streams_kafka_offset_monitoring_messages(dsm_processor, non_auto_commit_consumer, producer, kafka_topic):
    def _read_single_message(consumer):
        message = None
        while message is None or str(message.value()) != str(PAYLOAD):
            message = consumer.poll()
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
    first_offset = consumer.committed([TopicPartition(kafka_topic, 0)])[0].offset
    assert first_offset
    assert (
        list(buckets.values())[0].latest_commit_offsets[ConsumerPartitionKey("test_group", kafka_topic, 0)]
        == first_offset
    )

    _message = _read_single_message(consumer)  # noqa: F841
    assert consumer.committed([TopicPartition(kafka_topic, 0)])[0].offset == first_offset + 1
    assert (
        list(buckets.values())[0].latest_commit_offsets[ConsumerPartitionKey("test_group", kafka_topic, 0)]
        == first_offset + 1
    )


def test_data_streams_kafka_offset_monitoring_offsets(dsm_processor, non_auto_commit_consumer, producer, kafka_topic):
    def _read_single_message(consumer):
        message = None
        while message is None or str(message.value()) != str(PAYLOAD):
            message = consumer.poll()
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
    first_offset = consumer.committed([TopicPartition(kafka_topic, 0)])[0].offset
    assert first_offset > 0
    assert (
        list(buckets.values())[0].latest_commit_offsets[ConsumerPartitionKey("test_group", kafka_topic, 0)]
        == first_offset
    )

    _message = _read_single_message(consumer)  # noqa: F841
    assert consumer.committed([TopicPartition(kafka_topic, 0)])[0].offset == first_offset + 1
    assert (
        list(buckets.values())[0].latest_commit_offsets[ConsumerPartitionKey("test_group", kafka_topic, 0)]
        == first_offset + 1
    )


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
    first_offset = consumer.committed([TopicPartition(kafka_topic, 0)])[0].offset
    assert first_offset >= 1
    assert (
        list(buckets.values())[0].latest_commit_offsets[ConsumerPartitionKey("test_group", kafka_topic, 0)]
        == first_offset
    )

    _message = _read_single_message(consumer)  # noqa: F841
    consumer.commit(asynchronous=False)
    assert consumer.committed([TopicPartition(kafka_topic, 0)])[0].offset == first_offset + 1
    assert (
        list(buckets.values())[0].latest_commit_offsets[ConsumerPartitionKey("test_group", kafka_topic, 0)]
        == first_offset + 1
    )


def test_data_streams_kafka_produce_api_compatibility(dsm_processor, consumer, producer, empty_kafka_topic):
    kafka_topic = empty_kafka_topic

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


def test_data_streams_default_context_propagation(consumer, producer, kafka_topic):
    test_string = "context test"
    PAYLOAD = bytes(test_string, encoding="utf-8")

    producer.produce(kafka_topic, PAYLOAD, key="test_key")
    producer.flush()

    message = None
    while message is None or str(message.value()) != str(PAYLOAD):
        message = consumer.poll()

    # message comes back with expected test string
    assert message.value() == b"context test"

    # DSM header 'dd-pathway-ctx-base64' was propagated in the headers
    assert message.headers()[0][0] == PROPAGATION_KEY_BASE_64
    assert message.headers()[0][1] is not None


# It is not currently expected for kafka produce and consume spans to connect in a trace
def test_tracing_context_is_not_propagated_by_default(dummy_tracer, consumer, producer, kafka_topic):
    Pin.override(producer, tracer=dummy_tracer)
    Pin.override(consumer, tracer=dummy_tracer)

    test_string = "context test no propagation"
    test_key = "context test key no propagation"
    PAYLOAD = bytes(test_string, encoding="utf-8")

    producer.produce(kafka_topic, PAYLOAD, key=test_key)
    producer.flush()

    message = None
    while message is None or str(message.value()) != str(PAYLOAD):
        message = consumer.poll()

    # message comes back with expected test string
    assert message.value() == b"context test no propagation"

    consume_span = None
    traces = dummy_tracer.pop_traces()
    produce_span = traces[0][0]
    for trace in traces:
        for span in trace:
            if span.get_tag("kafka.received_message") == "True":
                if span.get_tag("kafka.message_key") == test_key:
                    consume_span = span

    # kafka.produce span is created without a parent
    assert produce_span.name == "kafka.produce"
    assert produce_span.parent_id is None
    assert produce_span.get_tag("pathway.hash") is not None

    # None of the kafka.consume spans have parents
    assert consume_span.name == "kafka.consume"
    assert consume_span.parent_id is None

    # None of these spans are part of the same trace
    assert produce_span.trace_id != consume_span.trace_id

    Pin.override(consumer, tracer=None)
    Pin.override(producer, tracer=None)


# Propagation should work when enabled
def test_tracing_context_is_propagated_when_enabled(ddtrace_run_python_code_in_subprocess):
    code = """
import pytest
import random
import sys

from ddtrace import Pin
from ddtrace.contrib.kafka.patch import patch

from tests.contrib.kafka.test_kafka import consumer
from tests.contrib.kafka.test_kafka import kafka_topic
from tests.contrib.kafka.test_kafka import producer
from tests.contrib.kafka.test_kafka import tracer
from tests.contrib.kafka.test_kafka import should_filter_empty_polls
from tests.utils import DummyTracer

def test(consumer, producer, kafka_topic):
    patch()
    dummy_tracer = DummyTracer()
    dummy_tracer.flush()
    Pin.override(producer, tracer=dummy_tracer)
    Pin.override(consumer, tracer=dummy_tracer)

    # use a random int in this string to prevent reading a message produced by a previous test run
    test_string = "context propagation enabled test " + str(random.randint(0, 1000))
    test_key = "context propagation key " + str(random.randint(0, 1000))
    PAYLOAD = bytes(test_string, encoding="utf-8")

    producer.produce(kafka_topic, PAYLOAD, key=test_key)
    producer.flush()

    message = None
    while message is None or str(message.value()) != str(PAYLOAD):
        message = consumer.poll()

    consume_span = None
    traces = dummy_tracer.pop_traces()
    produce_span = traces[0][0]
    for trace in traces:
        for span in trace:
            if span.get_tag('kafka.received_message') == 'True':
                if span.get_tag('kafka.message_key') == test_key:
                    consume_span = span
                    break

    assert str(message.value()) == str(PAYLOAD)

    # kafka.produce span is created without a parent
    assert produce_span.name == "kafka.produce"
    assert produce_span.parent_id is None

    # kafka.consume span has a parent
    assert consume_span.name == "kafka.consume"
    assert consume_span.parent_id == produce_span.span_id

    # Two of these spans are part of the same trace
    assert produce_span.trace_id == consume_span.trace_id

    Pin.override(consumer, tracer=None)
    Pin.override(producer, tracer=None)

if __name__ == "__main__":
    sys.exit(pytest.main(["-x", __file__]))
    """

    env = os.environ.copy()
    env["DD_KAFKA_PROPAGATION_ENABLED"] = "true"
    out, err, status, _ = ddtrace_run_python_code_in_subprocess(code, env=env)
    assert status == 0, out.decode() + err.decode()


def test_context_header_injection_works_no_client_added_headers(kafka_topic, producer, consumer):
    with override_config("kafka", dict(distributed_tracing_enabled=True)):
        # use a random int in this string to prevent reading a message produced by a previous test run
        test_string = "context propagation enabled test " + str(random.randint(0, 1000))
        test_key = "context propagation key " + str(random.randint(0, 1000))
        PAYLOAD = bytes(test_string, encoding="utf-8")

        producer.produce(kafka_topic, PAYLOAD, key=test_key)
        producer.flush()

        message = None
        while message is None or str(message.value()) != str(PAYLOAD):
            message = consumer.poll()

        propagation_asserted = False
        for header in message.headers():
            if header[0] == "x-datadog-trace-id":
                propagation_asserted = True

        assert propagation_asserted is True


def test_span_has_dsm_payload_hash(dummy_tracer, consumer, producer, kafka_topic):
    Pin.override(producer, tracer=dummy_tracer)
    Pin.override(consumer, tracer=dummy_tracer)

    test_string = "payload hash test"
    PAYLOAD = bytes(test_string, encoding="utf-8")

    producer.produce(kafka_topic, PAYLOAD, key="test_payload_hash_key")
    producer.flush()

    message = None
    while message is None or str(message.value()) != str(PAYLOAD):
        message = consumer.poll()

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

    Pin.override(consumer, tracer=None)
    Pin.override(producer, tracer=None)


def test_tracing_with_serialization_works(dummy_tracer, kafka_topic):
    def json_serializer(msg, s_obj):
        return json.dumps(msg).encode("utf-8")

    conf = {
        "bootstrap.servers": BOOTSTRAP_SERVERS,
        "key.serializer": json_serializer,
        "value.serializer": json_serializer,
    }
    _producer = confluent_kafka.SerializingProducer(conf)

    def json_deserializer(as_bytes, ctx):
        try:
            return json.loads(as_bytes)
        except json.decoder.JSONDecodeError:
            return  # return a type that has no __len__ because such types caused a crash at one point

    conf = {
        "bootstrap.servers": BOOTSTRAP_SERVERS,
        "group.id": GROUP_ID,
        "auto.offset.reset": "earliest",
        "key.deserializer": json_deserializer,
        "value.deserializer": json_deserializer,
    }

    _consumer = confluent_kafka.DeserializingConsumer(conf)
    tp = TopicPartition(kafka_topic, 0)
    tp.offset = 0  # we want to read the first message
    _consumer.commit(offsets=[tp])
    _consumer.subscribe([kafka_topic])

    Pin.override(_producer, tracer=dummy_tracer)
    Pin.override(_consumer, tracer=dummy_tracer)

    test_string = "serializing_test"
    PAYLOAD = {"val": test_string}

    _producer.produce(kafka_topic, key={"name": "keykey"}, value=PAYLOAD)
    _producer.flush()

    message = None
    while message is None or message.value() != PAYLOAD:
        message = _consumer.poll()

    # message comes back with expected test string
    assert message.value() == PAYLOAD

    traces = dummy_tracer.pop_traces()
    produce_span = traces[0][0]
    consume_span = traces[len(traces) - 1][0]

    assert produce_span.get_tag("kafka.message_key") is not None

    # consumer span will not have tag set since we can't serialize the deserialized key from the original type to
    # a string
    assert consume_span.get_tag("kafka.message_key") is None

    Pin.override(_consumer, tracer=None)
    Pin.override(_producer, tracer=None)


def test_traces_empty_poll_by_default(dummy_tracer, consumer, kafka_topic):
    Pin.override(consumer, tracer=dummy_tracer)

    message = "hello"
    while message is not None:
        message = consumer.poll(1.0)

    traces = dummy_tracer.pop_traces()

    empty_poll_span_created = False
    for trace in traces:
        for span in trace:
            try:
                assert span.name == "kafka.consume"
                assert span.get_tag("kafka.received_message") == "False"
                empty_poll_span_created = True
            except AssertionError:
                pass

    assert empty_poll_span_created is True

    Pin.override(consumer, tracer=None)


# Poll should not be traced when disabled
def test_does_not_trace_empty_poll_when_disabled(ddtrace_run_python_code_in_subprocess):
    code = """
import pytest
import random
import sys

from ddtrace import Pin
from ddtrace.contrib.kafka.patch import patch
from ddtrace import config

from tests.contrib.kafka.test_kafka import consumer
from tests.contrib.kafka.test_kafka import kafka_topic
from tests.contrib.kafka.test_kafka import producer
from tests.contrib.kafka.test_kafka import tracer
from tests.contrib.kafka.test_kafka import should_filter_empty_polls
from tests.utils import DummyTracer

def test(consumer, producer, kafka_topic):
    patch()
    dummy_tracer = DummyTracer()
    dummy_tracer.flush()
    Pin.override(producer, tracer=dummy_tracer)
    Pin.override(consumer, tracer=dummy_tracer)

    assert config.kafka.trace_empty_poll_enabled is False

    message = "hello"
    while message is not None:
        message = consumer.poll(1.0)

    traces = dummy_tracer.pop_traces()

    empty_poll_span_created = False
    for trace in traces:
        for span in trace:
            try:
                assert span.name == "kafka.consume"
                assert span.get_tag("kafka.received_message") == "False"
                empty_poll_span_created = True
            except AssertionError:
                pass

    assert empty_poll_span_created is False

    # produce a message now and ensure tracing for the consume works
    test_string = "empty poll disabled test"
    PAYLOAD = bytes(test_string, encoding="utf-8")

    producer.produce(kafka_topic, PAYLOAD, key="test_empty_poll_disabled")
    producer.flush()

    message = None
    while message is None or str(message.value()) != str(PAYLOAD):
        message = consumer.poll()

    traces = dummy_tracer.pop_traces()

    non_empty_poll_span_created = False
    for trace in traces:
        for span in trace:
            try:
                assert span.name == "kafka.consume"
                assert span.get_tag("kafka.received_message") == "True"
                non_empty_poll_span_created = True
            except AssertionError:
                pass

    assert non_empty_poll_span_created is True

    Pin.override(consumer, tracer=None)
    Pin.override(producer, tracer=None)

if __name__ == "__main__":
    sys.exit(pytest.main(["-x", __file__]))
    """
    env = os.environ.copy()
    env["DD_KAFKA_EMPTY_POLL_ENABLED"] = "False"
    out, err, status, _ = ddtrace_run_python_code_in_subprocess(code, env=env)
    assert status == 0, out.decode() + err.decode()
