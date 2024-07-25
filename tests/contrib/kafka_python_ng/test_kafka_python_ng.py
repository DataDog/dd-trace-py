import kafka
from kafka.structs import OffsetAndMetadata
import pytest

from ddtrace import Pin
from ddtrace.contrib.kafka_python_ng.patch import patch
from ddtrace.contrib.kafka_python_ng.patch import unpatch
import ddtrace.internal.datastreams  # noqa: F401 - used as part of mock patching
from tests.contrib.config import KAFKA_CONFIG
from tests.utils import DummyTracer
from tests.utils import override_config


GROUP_ID = "test_group"
BOOTSTRAP_SERVERS = "127.0.0.1:{}".format(KAFKA_CONFIG["port"])
KEY = bytes("test_key", encoding="utf-8")
PAYLOAD = bytes("hueh hueh hueh", encoding="utf-8")
DSM_TEST_PATH_HEADER_SIZE = 28


@pytest.fixture()
def kafka_topic(request):
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
def consumer(tracer, kafka_topic):
    _consumer = kafka.KafkaConsumer(
        bootstrap_servers=[BOOTSTRAP_SERVERS], auto_offset_reset="earliest", group_id=GROUP_ID, enable_auto_commit=False
    )
    _consumer.subscribe(topics=[kafka_topic])
    Pin.override(_consumer, tracer=tracer)
    return _consumer


@pytest.fixture
def dummy_tracer():
    patch()
    t = DummyTracer()
    # disable backoff because it makes these tests less reliable
    t._writer._send_payload_with_backoff = t._writer._send_payload
    yield t
    unpatch()


@pytest.fixture
def consumer_without_topic(tracer):
    _consumer = kafka.KafkaConsumer(
        bootstrap_servers=[BOOTSTRAP_SERVERS], auto_offset_reset="earliest", group_id=GROUP_ID, enable_auto_commit=False
    )
    Pin.override(_consumer, tracer=tracer)
    return _consumer


def test_commit(producer, consumer, kafka_topic):
    with override_config("kafka", dict(trace_empty_poll_enabled=False)):
        producer.send(kafka_topic, value=PAYLOAD, key=KEY)
        producer.flush()
        result = consumer.poll(1000)
        for topic_partition in result:
            for record in result[topic_partition]:
                consumer.commit({topic_partition: OffsetAndMetadata(record.offset, "")})


# Empty poll should be traced by default
def test_traces_empty_poll_by_default(dummy_tracer, consumer, kafka_topic):
    Pin.override(consumer, tracer=dummy_tracer)

    consumer.poll(10.0)

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
    with override_config("kafka", dict(trace_empty_poll_enabled=False)):
        Pin.override(consumer, tracer=dummy_tracer)

        # Test for empty poll
        consumer.poll(10.0)
        traces = dummy_tracer.pop_traces()
        assert 0 == len(traces)

        # Test for non-empty poll right after
        producer.send(kafka_topic, value=PAYLOAD, key=KEY)
        producer.flush()

        result = None
        while result is None:
            result = consumer.poll(10.0)

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
