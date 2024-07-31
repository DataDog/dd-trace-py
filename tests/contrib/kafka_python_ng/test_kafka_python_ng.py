import logging

import kafka
from kafka.structs import OffsetAndMetadata
from kafka.structs import TopicPartition
import pytest

from ddtrace import Pin
from ddtrace import Tracer
from ddtrace.contrib.kafka_python_ng.patch import patch
from ddtrace.contrib.kafka_python_ng.patch import unpatch
from ddtrace.filters import TraceFilter
import ddtrace.internal.datastreams  # noqa: F401 - used as part of mock patching
from tests.contrib.config import KAFKA_CONFIG
from tests.utils import DummyTracer
from tests.utils import override_config


logger = logging.getLogger(__name__)


GROUP_ID = "test_group"
BOOTSTRAP_SERVERS = "127.0.0.1:{}".format(KAFKA_CONFIG["port"])
KEY = bytes("test_key", encoding="utf-8")
PAYLOAD = bytes("hueh hueh hueh", encoding="utf-8")
DSM_TEST_PATH_HEADER_SIZE = 28
# Lowering the poll timeout will result in flaky tests
POLL_TIMEOUT = 8000


class KafkaConsumerPollFilter(TraceFilter):
    def process_trace(self, trace):
        # Filter out all poll spans that have no received message
        if trace[0].name == "kafka.consume" and trace[0].get_tag("kafka.received_message") == "False":
            return None

        return trace


@pytest.fixture()
def kafka_topic(request):
    topic_name = request.node.name.replace("[", "_").replace("]", "")
    logger.debug("Creating topic %s", topic_name)

    client = kafka.KafkaAdminClient(bootstrap_servers=[BOOTSTRAP_SERVERS])
    try:
        client.delete_topics([topic_name])
    except kafka.errors.UnknownTopicOrPartitionError:
        pass

    client.create_topics([kafka.admin.NewTopic(topic_name, 1, 1)])
    return topic_name


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
def producer(tracer):
    logger.debug("Creating producer")
    _producer = kafka.KafkaProducer(bootstrap_servers=[BOOTSTRAP_SERVERS])
    Pin.override(_producer, tracer=tracer)
    return _producer


@pytest.fixture
def consumer(tracer, kafka_topic):
    logger.debug("Creating consumer")
    _consumer = kafka.KafkaConsumer(
        bootstrap_servers=[BOOTSTRAP_SERVERS],
        auto_offset_reset="earliest",
        group_id=GROUP_ID,
    )
    logger.debug("Resetting offset for topic %s", kafka_topic)
    tp = TopicPartition(kafka_topic, 0)
    _consumer.commit({tp: OffsetAndMetadata(0, "")})
    Pin.override(_consumer, tracer=tracer)
    logger.debug("Subscribing to topic")
    _consumer.subscribe(topics=[kafka_topic])
    yield _consumer
    _consumer.close()
    logger.debug("Consumer closed")


@pytest.fixture
def non_auto_commit_consumer(tracer, kafka_topic):
    _consumer = kafka.KafkaConsumer(
        bootstrap_servers=[BOOTSTRAP_SERVERS], auto_offset_reset="earliest", group_id=GROUP_ID, enable_auto_commit=False
    )

    tp = TopicPartition(kafka_topic, 0)
    _consumer.commit({tp: OffsetAndMetadata(0, "")})
    Pin.override(_consumer, tracer=tracer)
    _consumer.subscribe(topics=[kafka_topic])
    yield _consumer
    _consumer.close()


def test_send_single_server(dummy_tracer, producer, kafka_topic):
    Pin.override(producer, tracer=dummy_tracer)
    producer.send(kafka_topic, value=PAYLOAD, key=KEY)
    producer.close()

    traces = dummy_tracer.pop_traces()
    assert 1 == len(traces)
    produce_span = traces[0][0]
    assert produce_span.get_tag("messaging.kafka.bootstrap.servers") == BOOTSTRAP_SERVERS
    Pin.override(producer, tracer=None)


def test_send_multiple_servers(dummy_tracer, kafka_topic):
    producer = kafka.KafkaProducer(bootstrap_servers=[BOOTSTRAP_SERVERS] * 3)
    Pin.override(producer, tracer=dummy_tracer)
    producer.send(kafka_topic, value=PAYLOAD, key=KEY)
    producer.close()

    traces = dummy_tracer.pop_traces()
    assert 1 == len(traces)
    produce_span = traces[0][0]
    assert produce_span.get_tag("messaging.kafka.bootstrap.servers") == ",".join([BOOTSTRAP_SERVERS] * 3)
    Pin.override(producer, tracer=None)


def test_send_none_key(dummy_tracer, producer, kafka_topic):
    Pin.override(producer, tracer=dummy_tracer)
    producer.send(kafka_topic, value=PAYLOAD, key=None)
    producer.close()

    traces = dummy_tracer.pop_traces()
    assert 1 == len(traces), "key=None does not cause send() call to raise an exception"
    Pin.override(producer, tracer=None)


@pytest.mark.parametrize("tombstone", [False, True])
@pytest.mark.snapshot(ignores=["metrics.kafka.message_offset"])
def test_message(producer, tombstone, kafka_topic):
    with override_config("kafka", dict(trace_empty_poll_enabled=False)):
        if tombstone:
            producer.send(kafka_topic, value=None, key=KEY)
        else:
            producer.send(kafka_topic, value=PAYLOAD, key=KEY)
        producer.close()


@pytest.mark.snapshot(ignores=["metrics.kafka.message_offset"])
def test_commit_with_poll(producer, consumer, kafka_topic):
    logger.debug("Starting test_commit_with_poll")
    with override_config("kafka", dict(trace_empty_poll_enabled=False)):
        logger.debug("Sending data")
        res = producer.send(kafka_topic, value=PAYLOAD, key=KEY)
        logger.debug("data result: %s", res)
        logger.debug("Closing producer")
        producer.close()
        logger.debug("Producer closed")
        logger.debug("Starting poll")
        result = consumer.poll(POLL_TIMEOUT)
        logger.debug("poll finished: %s", result)
        assert len(result) == 1
        for topic_partition in result:
            for record in result[topic_partition]:
                consumer.commit({topic_partition: OffsetAndMetadata(record.offset + 1, "")})
    logger.debug("Stopping test_commit_with_poll")


def test_commit_with_poll_single_message(dummy_tracer, producer, consumer, kafka_topic):
    logger.debug("Starting test_commit_with_poll_single_message")
    Pin.override(consumer, tracer=dummy_tracer)
    with override_config("kafka", dict(trace_empty_poll_enabled=False)):
        logger.debug("Sending data")
        res = producer.send(kafka_topic, value=PAYLOAD, key=KEY)
        logger.debug("data results %s", res)
        logger.debug("Closing producer")
        producer.close()
        logger.debug("Producer closed")
        # One message is consumed and one span is generated.
        logger.debug("Starting poll")
        result = consumer.poll(timeout_ms=POLL_TIMEOUT, max_records=1)
        logger.debug("Poll finished %s", result)
        assert len(result) == 1
        topic_partition = list(result.keys())[0]
        assert len(result[topic_partition]) == 1
        consumer.commit({topic_partition: OffsetAndMetadata(result[topic_partition][0].offset + 1, "")})
    logger.debug("Kafka part finished")
    logger.debug("Starting datadog span assertions")
    traces = dummy_tracer.pop_traces()
    assert len(traces) == 1

    span = traces[0][0]
    assert span.name == "kafka.consume"
    assert span.get_tag("kafka.received_message") == "True"
    Pin.override(consumer, tracer=dummy_tracer)
    logger.debug("Test finished")


def test_commit_with_poll_multiple_messages(dummy_tracer, producer, consumer, kafka_topic):
    Pin.override(consumer, tracer=dummy_tracer)
    with override_config("kafka", dict(trace_empty_poll_enabled=False)):
        producer.send(kafka_topic, value=PAYLOAD, key=KEY)
        producer.send(kafka_topic, value=PAYLOAD, key=KEY)
        producer.close()
        # Two messages are consumed but only ONE span is generated
        result = consumer.poll(timeout_ms=POLL_TIMEOUT, max_records=2)
        assert len(result) == 1
        topic_partition = list(result.keys())[0]
        assert len(result[topic_partition]) == 2
        consumer.commit({topic_partition: OffsetAndMetadata(result[topic_partition][1].offset + 1, "")})

    traces = dummy_tracer.pop_traces()
    # 1 trace and 1 span
    assert len(traces) == 1
    assert len(traces[0]) == 1

    span = traces[0][0]
    assert span.name == "kafka.consume"
    assert span.get_tag("kafka.received_message") == "True"
    Pin.override(consumer, tracer=None)


@pytest.mark.snapshot(ignores=["metrics.kafka.message_offset"])
def test_async_commit(producer, consumer, kafka_topic):
    with override_config("kafka", dict(trace_empty_poll_enabled=False)):
        producer.send(kafka_topic, value=PAYLOAD, key=KEY)
        producer.close()
        result = consumer.poll(POLL_TIMEOUT)
        assert len(result) == 1
        topic_partition = list(result.keys())[0]
        consumer.commit_async({topic_partition: OffsetAndMetadata(result[topic_partition][0].offset, "")})


# Empty poll should be traced by default
def test_traces_empty_poll_by_default(dummy_tracer, consumer, kafka_topic):
    Pin.override(consumer, tracer=dummy_tracer)

    consumer.poll(POLL_TIMEOUT)

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


# Empty poll should not be traced when disabled
def test_does_not_trace_empty_poll_when_disabled(dummy_tracer, consumer, producer, kafka_topic):
    Pin.override(consumer, tracer=dummy_tracer)
    with override_config("kafka", dict(trace_empty_poll_enabled=False)):
        # Test for empty poll
        consumer.poll(POLL_TIMEOUT)

        traces = dummy_tracer.pop_traces()
        assert 0 == len(traces)

        # Test for non-empty poll right after
        producer.send(kafka_topic, value=PAYLOAD, key=KEY)
        producer.close()

        result = None
        while result is None:
            result = consumer.poll(POLL_TIMEOUT)

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
