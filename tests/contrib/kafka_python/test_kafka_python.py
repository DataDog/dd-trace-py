import pytest

from ddtrace.contrib.internal.kafka_python.patch import patch
from ddtrace.contrib.internal.kafka_python.patch import unpatch

from .utils import PAYLOAD
from .utils import create_topic
from .utils import iterate_records
from .utils import make_consumer
from .utils import make_producer
from .utils import poll_records


@pytest.fixture(autouse=True)
def patch_kafka_python():
    patch()
    yield
    unpatch()


def _spans_named(test_spans, name):
    return [span for span in test_spans.pop() if span.name == name]


def test_produce_span(test_spans):
    topic = create_topic("produce_span")
    producer = make_producer()
    try:
        metadata = producer.send(topic, value=PAYLOAD, key=b"k").get(timeout=30)
    finally:
        producer.close()

    (span,) = _spans_named(test_spans, "kafka.produce")
    assert span.get_tag("component") == "kafka_python"
    assert span.get_tag("span.kind") == "producer"
    assert span.get_tag("kafka.topic") == topic
    assert span.get_tag("kafka.message_key") == "k"
    assert span.get_metric("kafka.message_offset") == metadata.offset
    assert span.error == 0


def test_produce_span_error(test_spans):
    topic = create_topic("produce_span_error")
    producer = make_producer()
    try:
        with pytest.raises((TypeError, AssertionError)):
            producer.send(topic, value=PAYLOAD, headers="not a list")
    finally:
        producer.close()

    (span,) = _spans_named(test_spans, "kafka.produce")
    assert span.error == 1


@pytest.mark.parametrize("read", [iterate_records, poll_records])
def test_consume_span(test_spans, read):
    topic = create_topic("consume_span")
    producer = make_producer()
    try:
        producer.send(topic, value=PAYLOAD).get(timeout=30)
    finally:
        producer.close()
    test_spans.pop()

    consumer = make_consumer(topic)
    try:
        read(consumer, 1)
    finally:
        consumer.close(autocommit=False)

    spans = [
        span for span in _spans_named(test_spans, "kafka.consume") if span.get_tag("kafka.received_message") == "True"
    ]
    assert len(spans) == 1
    assert spans[0].get_tag("component") == "kafka_python"
    assert spans[0].get_tag("span.kind") == "consumer"
    assert spans[0].get_tag("kafka.topic") == topic


@pytest.mark.subprocess(env=dict(DD_DATA_STREAMS_ENABLED="false"), err=None)
def test_no_dsm_headers_when_disabled():
    from ddtrace.contrib.internal.kafka_python.patch import patch
    from ddtrace.internal.datastreams.processor import PROPAGATION_KEY_BASE_64
    from tests.contrib.kafka_python.utils import create_topic
    from tests.contrib.kafka_python.utils import iterate_records
    from tests.contrib.kafka_python.utils import make_consumer
    from tests.contrib.kafka_python.utils import make_producer

    patch()
    topic = create_topic("dsm_disabled")
    producer = make_producer()
    producer.send(topic, value=b"v").get(timeout=30)
    producer.close()

    consumer = make_consumer(topic)
    (record,) = iterate_records(consumer, 1)
    consumer.close(autocommit=False)

    assert PROPAGATION_KEY_BASE_64 not in [key for key, _ in record.headers]


@pytest.mark.parametrize("trace_empty_poll", [True, False])
def test_empty_poll_span(test_spans, trace_empty_poll):
    from ddtrace import config

    topic = create_topic("empty_poll")
    consumer = make_consumer(topic)
    original = config.kafka_python.trace_empty_poll_enabled
    config.kafka_python.trace_empty_poll_enabled = trace_empty_poll
    try:
        assert consumer.poll(timeout_ms=100) == {}
    finally:
        config.kafka_python.trace_empty_poll_enabled = original
        consumer.close(autocommit=False)

    spans = _spans_named(test_spans, "kafka.consume")
    assert len(spans) == (1 if trace_empty_poll else 0)


def test_produce_span_not_active_after_send(test_spans, tracer):
    topic = create_topic("active_span_after_send")
    producer = make_producer()
    try:
        with tracer.trace("caller.work") as parent:
            future = producer.send(topic, value=PAYLOAD)
            assert tracer.current_span() is parent
            with tracer.trace("after.send"):
                pass
        future.get(timeout=30)
    finally:
        producer.close()

    spans = {span.name: span for span in test_spans.pop()}
    assert spans["kafka.produce"].parent_id == parent.span_id
    assert spans["after.send"].parent_id == parent.span_id


def test_send_error_without_traceback(test_spans):
    from kafka.errors import KafkaTimeoutError
    from kafka.future import Future

    from ddtrace.contrib.internal.kafka_python.patch import traced_send

    topic = create_topic("send_error_without_traceback")
    producer = make_producer()
    try:
        future = Future()
        error = KafkaTimeoutError("delivery failed")
        assert error.__traceback__ is None
        traced_send(lambda *args, **kwargs: future, producer, (topic,), {})
        future.failure(error)
    finally:
        producer.close()

    (span,) = _spans_named(test_spans, "kafka.produce")
    assert span.error == 1
    assert span.get_tag("error.type") == "kafka.errors.KafkaTimeoutError"
    assert "delivery failed" in span.get_tag("error.message")
    assert span.get_tag("error.stack")


def test_iterator_error_records_consume_span(test_spans):
    from kafka.errors import KafkaError

    from ddtrace.contrib.internal.kafka_python.patch import traced_next

    def fail():
        raise KafkaError("fetch failed")

    def exhausted():
        raise StopIteration

    consumer = make_consumer(create_topic("iterator_error"))
    try:
        with pytest.raises(KafkaError):
            traced_next(fail, consumer, (), {})
        with pytest.raises(StopIteration):
            traced_next(exhausted, consumer, (), {})
    finally:
        consumer.close(autocommit=False)

    (span,) = _spans_named(test_spans, "kafka.consume")
    assert span.error == 1
    assert span.get_tag("error.type") == "kafka.errors.KafkaError"
    assert "fetch failed" in span.get_tag("error.message")
