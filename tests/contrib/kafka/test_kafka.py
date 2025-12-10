import json
import logging
import os
import random
import time

import confluent_kafka
from confluent_kafka import TopicPartition
import pytest

from ddtrace import config
from ddtrace._trace.pin import Pin
from ddtrace.contrib.internal.kafka.patch import TracedConsumer
from ddtrace.contrib.internal.kafka.patch import TracedProducer
from ddtrace.contrib.internal.kafka.patch import patch
from ddtrace.contrib.internal.kafka.patch import unpatch
from ddtrace.internal.utils.retry import fibonacci_backoff_with_jitter
from tests.utils import override_config

from .conftest import BOOTSTRAP_SERVERS
from .conftest import GROUP_ID
from .conftest import KEY
from .conftest import KafkaConsumerPollFilter


PAYLOAD = bytes("hueh hueh hueh", encoding="utf-8")
SNAPSHOT_IGNORES = [
    "metrics.kafka.message_offset",
    "meta.error.stack",
    "meta.error.message",
    "meta.messaging.kafka.bootstrap.servers",
    "meta.peer.service",
]


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


def test_consumer_initialized_with_unpacked_config(tracer):
    """Test that adding a logger to a Consumer init does not raise any errors."""
    consumer = confluent_kafka.Consumer(
        **{
            "bootstrap.servers": BOOTSTRAP_SERVERS,
            "group.id": GROUP_ID,
            "auto.offset.reset": "earliest",
        },
    )
    assert isinstance(consumer, TracedConsumer)
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


@pytest.mark.parametrize(
    "config,expect_servers",
    [
        ({"bootstrap.servers": BOOTSTRAP_SERVERS}, BOOTSTRAP_SERVERS),
        ({"metadata.broker.list": BOOTSTRAP_SERVERS}, BOOTSTRAP_SERVERS),
        ({}, None),
    ],
)
def test_producer_initialized_unpacked_config(config, expect_servers, tracer):
    producer = confluent_kafka.Producer(**config)
    assert isinstance(producer, TracedProducer)
    if expect_servers is not None:
        assert producer._dd_bootstrap_servers == expect_servers
    else:
        assert producer._dd_bootstrap_servers is None


def test_produce_single_server(dummy_tracer, producer, kafka_topic):
    Pin._override(producer, tracer=dummy_tracer)
    producer.produce(kafka_topic, PAYLOAD, key=KEY)
    producer.flush()

    traces = dummy_tracer.pop_traces()
    assert 1 == len(traces)
    produce_span = traces[0][0]
    assert produce_span.get_tag("messaging.kafka.bootstrap.servers") == BOOTSTRAP_SERVERS


def test_produce_none_key(dummy_tracer, producer, kafka_topic):
    Pin._override(producer, tracer=dummy_tracer)
    producer.produce(kafka_topic, PAYLOAD, key=None)
    producer.flush()

    traces = dummy_tracer.pop_traces()
    assert 1 == len(traces), "key=None does not cause produce() call to raise an exception"
    Pin._override(producer, tracer=None)


def test_produce_multiple_servers(dummy_tracer, kafka_topic):
    producer = confluent_kafka.Producer({"bootstrap.servers": ",".join([BOOTSTRAP_SERVERS] * 3)})
    Pin._override(producer, tracer=dummy_tracer)
    producer.produce(kafka_topic, PAYLOAD, key=KEY)
    producer.flush()

    traces = dummy_tracer.pop_traces()
    assert 1 == len(traces)
    produce_span = traces[0][0]
    assert produce_span.get_tag("messaging.kafka.bootstrap.servers") == ",".join([BOOTSTRAP_SERVERS] * 3)
    Pin._override(producer, tracer=None)


def test_produce_topicname(dummy_tracer, producer, kafka_topic):
    Pin._override(producer, tracer=dummy_tracer)
    producer.produce(kafka_topic, PAYLOAD, key=KEY)
    producer.flush()

    traces = dummy_tracer.pop_traces()
    assert 1 == len(traces)
    produce_span = traces[0][0]
    assert produce_span.get_tag("messaging.destination.name") == kafka_topic


@pytest.mark.parametrize("tombstone", [False, True])
@pytest.mark.snapshot(ignores=SNAPSHOT_IGNORES)
def test_message(producer, consumer, tombstone, kafka_topic):
    with override_config("kafka", dict(trace_empty_poll_enabled=False)):
        if tombstone:
            producer.produce(kafka_topic, key=KEY)
        else:
            producer.produce(kafka_topic, PAYLOAD, key=KEY)
        producer.flush()


@pytest.mark.snapshot(ignores=SNAPSHOT_IGNORES)
def test_commit(producer, consumer, kafka_topic):
    with override_config("kafka", dict(trace_empty_poll_enabled=False)):
        producer.produce(kafka_topic, PAYLOAD, key=KEY)
        producer.flush()
        message = consumer.poll()
        consumer.commit(message)


@pytest.mark.snapshot(ignores=SNAPSHOT_IGNORES)
def test_commit_with_consume_single_message(producer, consumer, kafka_topic):
    with override_config("kafka", dict(trace_empty_poll_enabled=False)):
        producer.produce(kafka_topic, PAYLOAD, key=KEY)
        producer.flush()
        # One message is consumed and one span is generated.
        messages = consumer.consume(num_messages=1)
        assert len(messages) == 1
        consumer.commit(messages[0])


@pytest.mark.snapshot(ignores=SNAPSHOT_IGNORES)
def test_commit_with_consume_with_multiple_messages(producer, consumer, kafka_topic):
    with override_config("kafka", dict(trace_empty_poll_enabled=False)):
        producer.produce(kafka_topic, PAYLOAD, key=KEY)
        producer.produce(kafka_topic, PAYLOAD, key=KEY)
        producer.flush()
        # Two messages are consumed but only ONE span is generated
        messages = consumer.consume(num_messages=2)
        assert len(messages) == 2


@pytest.mark.snapshot(ignores=SNAPSHOT_IGNORES)
@pytest.mark.parametrize("should_filter_empty_polls", [False])
@pytest.mark.skip(reason="FIXME: This test requires the initialization of a new tracer. This is not supported")
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


@pytest.mark.snapshot(ignores=SNAPSHOT_IGNORES)
def test_commit_with_offset(producer, consumer, kafka_topic):
    with override_config("kafka", dict(trace_empty_poll_enabled=False)):
        producer.produce(kafka_topic, PAYLOAD, key=KEY)
        producer.flush()
        consumer.poll()
        consumer.commit(offsets=[TopicPartition(kafka_topic)])


@pytest.mark.snapshot(ignores=SNAPSHOT_IGNORES)
def test_commit_with_only_async_arg(producer, consumer, kafka_topic):
    with override_config("kafka", dict(trace_empty_poll_enabled=False)):
        producer.produce(kafka_topic, PAYLOAD, key=KEY)
        producer.flush()
        consumer.poll()
        consumer.commit(asynchronous=False)


@pytest.mark.snapshot(token="tests.contrib.kafka.test_kafka.test_service_override", ignores=SNAPSHOT_IGNORES)
def test_service_override_config(producer, consumer, kafka_topic):
    with override_config("kafka", dict(service="my-custom-service-name", trace_empty_poll_enabled=False)):
        producer.produce(kafka_topic, PAYLOAD, key=KEY)
        producer.flush()
        consumer.poll()


def retry_until_not_none(factory):
    for _ in range(10):
        x = factory()
        if x is not None:
            return x
        time.sleep(0.1)
    return None


def _generate_in_subprocess(random_topic):
    import ddtrace
    from ddtrace.contrib.internal.kafka.patch import patch
    from ddtrace.contrib.internal.kafka.patch import unpatch

    PAYLOAD = bytes("hueh hueh hueh", encoding="utf-8")

    ddtrace.tracer.configure(trace_processors=[KafkaConsumerPollFilter()])
    # disable backoff because it makes these tests less reliable
    if not config._trace_writer_native:
        ddtrace.tracer._span_aggregator.writer._send_payload_with_backoff = (
            ddtrace.tracer._span_aggregator.writer._send_payload
        )
    patch()

    producer = confluent_kafka.Producer({"bootstrap.servers": BOOTSTRAP_SERVERS})
    consumer = confluent_kafka.Consumer(
        {
            "bootstrap.servers": BOOTSTRAP_SERVERS,
            "group.id": "test_group",
            "auto.offset.reset": "earliest",
        }
    )
    Pin._override(producer, tracer=ddtrace.tracer)
    Pin._override(consumer, tracer=ddtrace.tracer)

    # We run all of these commands with retry attempts because the kafka-confluent API
    # sys.exits on connection failures, which causes the test to fail. We want to retry
    # until the connection is established. Connection failures are somewhat common.
    fibonacci_backoff_with_jitter(5)(consumer.subscribe)([random_topic])
    fibonacci_backoff_with_jitter(5)(producer.produce)(random_topic, PAYLOAD, key="test_key")
    fibonacci_backoff_with_jitter(5, until=lambda result: isinstance(result, int))(producer.flush)()
    consumer.poll()

    unpatch()
    consumer.close()


@pytest.mark.snapshot(token="tests.contrib.kafka.test_kafka.test_service_override_env_var", ignores=SNAPSHOT_IGNORES)
def test_service_override_env_var(ddtrace_run_python_code_in_subprocess, kafka_topic):
    code = """
import sys
import pytest
from tests.contrib.kafka.test_kafka import _generate_in_subprocess
from tests.contrib.kafka.conftest import kafka_topic


def test():
    _generate_in_subprocess("{}")

if __name__ == "__main__":
    sys.exit(pytest.main(["-x", __file__]))
    """.format(kafka_topic)
    env = os.environ.copy()
    env["DD_KAFKA_SERVICE"] = "my-custom-service-name"
    env["DD_KAFKA_EMPTY_POLL_ENABLED"] = "False"
    out, err, status, _ = ddtrace_run_python_code_in_subprocess(code, env=env)
    assert status == 0, out.decode()
    assert err == b"", err.decode()


@pytest.mark.snapshot(ignores=SNAPSHOT_IGNORES)
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
    """.format(kafka_topic)
    env = os.environ.copy()
    if service:
        env["DD_SERVICE"] = service
    if schema:
        env["DD_TRACE_SPAN_ATTRIBUTE_SCHEMA"] = schema
    env["DD_KAFKA_EMPTY_POLL_ENABLED"] = "False"
    out, err, status, _ = ddtrace_run_python_code_in_subprocess(code, env=env)
    assert status == 0, out.decode()
    assert err == b"", err.decode()


# It is not currently expected for kafka produce and consume spans to connect in a trace
def test_tracing_context_is_not_propagated_by_default(dummy_tracer, consumer, producer, kafka_topic):
    Pin._override(producer, tracer=dummy_tracer)
    Pin._override(consumer, tracer=dummy_tracer)

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

    Pin._override(consumer, tracer=None)
    Pin._override(producer, tracer=None)


# Propagation should work when enabled
def test_tracing_context_is_propagated_when_enabled(ddtrace_run_python_code_in_subprocess):
    code = """
import pytest
import random
import sys

from ddtrace._trace.pin import Pin
from ddtrace.contrib.internal.kafka.patch import patch
from tests.contrib.kafka.conftest import consumer
from tests.contrib.kafka.conftest import kafka_topic
from tests.contrib.kafka.conftest import producer
from tests.contrib.kafka.conftest import tracer
from tests.contrib.kafka.conftest import should_filter_empty_polls

from tests.utils import DummyTracer

def test(consumer, producer, kafka_topic):
    patch()
    dummy_tracer = DummyTracer()
    dummy_tracer.flush()
    Pin._override(producer, tracer=dummy_tracer)
    Pin._override(consumer, tracer=dummy_tracer)

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

    Pin._override(consumer, tracer=None)
    Pin._override(producer, tracer=None)

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


def test_consumer_uses_active_context_when_no_valid_distributed_context_exists(
    kafka_topic, producer, consumer, dummy_tracer
):
    # use a random int in this string to prevent reading a message produced by a previous test run
    test_string = "producer does not inject context test " + str(random.randint(0, 1000))
    test_key = "producer does not inject context test " + str(random.randint(0, 1000))
    PAYLOAD = bytes(test_string, encoding="utf-8")

    producer.produce(kafka_topic, PAYLOAD, key=test_key)
    producer.flush()

    Pin._override(consumer, tracer=dummy_tracer)

    with dummy_tracer.trace("kafka consumer parent span") as parent_span:
        with override_config("kafka", dict(distributed_tracing_enabled=True)):
            message = None
            while message is None or str(message.value()) != str(PAYLOAD):
                message = consumer.poll()

    traces = dummy_tracer.pop_traces()
    consume_span = traces[len(traces) - 1][-1]

    # assert consumer_span parent is our custom span
    assert consume_span.name == "kafka.consume"
    assert consume_span.parent_id == parent_span.span_id

    Pin._override(consumer, tracer=None)


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

    Pin._override(_producer, tracer=dummy_tracer)
    Pin._override(_consumer, tracer=dummy_tracer)

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

    Pin._override(_consumer, tracer=None)
    Pin._override(_producer, tracer=None)


def test_traces_empty_poll_by_default(dummy_tracer, consumer, kafka_topic):
    Pin._override(consumer, tracer=dummy_tracer)

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

    Pin._override(consumer, tracer=None)


# Poll should not be traced when disabled
def test_does_not_trace_empty_poll_when_disabled(ddtrace_run_python_code_in_subprocess):
    code = """
import pytest
import random
import sys

from ddtrace._trace.pin import Pin
from ddtrace.contrib.internal.kafka.patch import patch
from ddtrace import config

from tests.contrib.kafka.conftest import consumer
from tests.contrib.kafka.conftest import kafka_topic
from tests.contrib.kafka.conftest import producer
from tests.contrib.kafka.conftest import tracer
from tests.contrib.kafka.conftest import should_filter_empty_polls
from tests.utils import DummyTracer

def test(consumer, producer, kafka_topic):
    patch()
    dummy_tracer = DummyTracer()
    dummy_tracer.flush()
    Pin._override(producer, tracer=dummy_tracer)
    Pin._override(consumer, tracer=dummy_tracer)

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

    Pin._override(consumer, tracer=None)
    Pin._override(producer, tracer=None)

if __name__ == "__main__":
    sys.exit(pytest.main(["-x", __file__]))
    """
    env = os.environ.copy()
    env["DD_KAFKA_EMPTY_POLL_ENABLED"] = "False"
    out, err, status, _ = ddtrace_run_python_code_in_subprocess(code, env=env)
    assert status == 0, out.decode() + err.decode()


def test_cluster_id_failure_caching(dummy_tracer, kafka_topic):
    """Test that _get_cluster_id caches failures and doesn't repeatedly timeout when cluster is down."""
    import time

    from ddtrace.contrib.internal.kafka.patch import _get_cluster_id

    # Create a producer with an incorrect server address to simulate cluster being down
    producer_with_bad_address = confluent_kafka.Producer(
        {
            "bootstrap.servers": "non-existent-kafka-host:9092",
            "socket.timeout.ms": 1000,
        }
    )
    Pin._override(producer_with_bad_address, tracer=dummy_tracer)

    start_time = time.time()
    result1 = _get_cluster_id(producer_with_bad_address, kafka_topic)
    elapsed_time1 = time.time() - start_time

    assert result1 is None
    # Should take approximately 1 second (our timeout)
    assert 0.5 < elapsed_time1 < 2.0, f"First call took {elapsed_time1} seconds, expected ~1 second"

    assert hasattr(producer_with_bad_address, "_dd_cluster_id_failure_time")
    assert producer_with_bad_address._dd_cluster_id_failure_time > 0

    # Second call should return None immediately
    start_time = time.time()
    result2 = _get_cluster_id(producer_with_bad_address, kafka_topic)
    elapsed_time2 = time.time() - start_time

    assert result2 is None
    assert elapsed_time2 < 0.1, f"Second call took {elapsed_time2} seconds, expected < 0.1 seconds"

    producer_with_bad_address._dd_cluster_id_failure_time = 0  # Simulate 5 minutes passing

    start_time = time.time()
    result3 = _get_cluster_id(producer_with_bad_address, kafka_topic)
    elapsed_time3 = time.time() - start_time

    assert result3 is None
    # Should timeout again after ~1 second
    assert 0.5 < elapsed_time3 < 2.0, f"Third call took {elapsed_time3} seconds, expected ~1 second"


def test_cluster_id_success_caching(dummy_tracer, producer, kafka_topic):
    """Test that successful cluster ID retrieval is cached."""
    from ddtrace.contrib.internal.kafka.patch import _get_cluster_id

    # Set up counting wrapper
    original_list_topics = producer.list_topics
    call_count = 0

    def counting_list_topics(*args, **kwargs):
        nonlocal call_count
        call_count += 1
        return original_list_topics(*args, **kwargs)

    producer.list_topics = counting_list_topics

    # First call should get the cluster ID from the real cluster
    result1 = _get_cluster_id(producer, kafka_topic)
    assert result1 is not None
    assert hasattr(producer, "_dd_cluster_id")
    assert producer._dd_cluster_id == result1
    assert call_count == 1

    # Second call should use cached value
    result2 = _get_cluster_id(producer, kafka_topic)
    assert result2 == result1
    assert call_count == 1  # Should still be 1 (used cache)
