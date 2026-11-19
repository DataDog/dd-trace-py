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
