import threading

import pytest

from ddtrace.trace import tracer
from tests.utils import override_config


SNAPSHOT_IGNORES = [
    "meta.runtime-id",
    "meta._dd.p.tid",
    "meta._dd.p.dm",
    "metrics.process_id",
    "meta.messaging.message_id",
    "meta._dd.span_links",
]

TRACE_CONTEXT_KEYS = [
    "x-datadog-trace-id",
    "x-datadog-parent-id",
    "x-datadog-sampling-priority",
    "x-datadog-tags",
    "traceparent",
    "tracestate",
]


@pytest.mark.snapshot(ignores=SNAPSHOT_IGNORES)
def test_publish(publisher, topic_path):
    with tracer.trace("parent.span"):
        future = publisher.publish(topic_path, b"Hello World")
        message_id = future.result(timeout=10)
        assert message_id is not None


@pytest.mark.snapshot(ignores=SNAPSHOT_IGNORES)
def test_batch_publish(batch_publisher, topic_path):
    """Test that publishing multiple messages in a batch produces individual spans for each publish call."""
    with tracer.trace("parent.span"):
        futures = []
        for msg in [b"Hello", b"World", b"Batch"]:
            futures.append(batch_publisher.publish(topic_path, msg))

        for future in futures:
            message_id = future.result(timeout=10)
            assert message_id is not None


@pytest.mark.snapshot(ignores=SNAPSHOT_IGNORES + ["meta.error.stack", "meta.error.message"])
def test_publish_future_error(publisher):
    """Test that the span records error info when the publish future fails."""
    nonexistent_topic = "projects/test-project/topics/nonexistent-topic"
    callbacks_done = threading.Event()
    with tracer.trace("parent.span"):
        future = publisher.publish(nonexistent_topic, b"Hello World")
        future.add_done_callback(lambda _: callbacks_done.set())
        callbacks_done.wait(timeout=10)
        assert future.exception() is not None


def test_propagation_enabled(publisher, topic_path, subscriber, subscription_path):
    """Test that Datadog trace context keys are injected into message attributes when propagation is enabled."""
    with tracer.trace("parent.span"):
        future = publisher.publish(topic_path, b"Hello World")
        future.result(timeout=10)

    response = subscriber.pull(subscription=subscription_path, max_messages=1, timeout=10)
    assert len(response.received_messages) == 1
    assert all(key in dict(response.received_messages[0].message.attributes) for key in TRACE_CONTEXT_KEYS)


def test_batch_propagation(batch_publisher, topic_path, subscriber, subscription_path):
    """Test that each message in a batch gets its own trace context injected."""
    messages = [b"Hello", b"World", b"Batch"]
    with tracer.trace("parent.span"):
        futures = []
        for msg in messages:
            futures.append(batch_publisher.publish(topic_path, msg))
        for future in futures:
            future.result(timeout=10)

    response = subscriber.pull(subscription=subscription_path, max_messages=len(messages), timeout=10)
    assert len(response.received_messages) == len(messages)
    assert all(key in dict(m.message.attributes) for m in response.received_messages for key in TRACE_CONTEXT_KEYS)


def test_propagation_preserves_user_attributes(publisher, topic_path, subscriber, subscription_path):
    """Test that user-provided message attributes are preserved alongside injected trace context."""
    with tracer.trace("parent.span"):
        future = publisher.publish(topic_path, b"Hello World", custom_key="custom_value", another="attr")
        future.result(timeout=10)

    response = subscriber.pull(subscription=subscription_path, max_messages=1, timeout=10)
    assert len(response.received_messages) == 1
    attributes = dict(response.received_messages[0].message.attributes)
    assert attributes["custom_key"] == "custom_value"
    assert attributes["another"] == "attr"
    assert all(key in attributes for key in TRACE_CONTEXT_KEYS)


def test_propagation_disabled(publisher, topic_path, subscriber, subscription_path):
    """Test that trace context keys are NOT injected when propagation is disabled."""
    with override_config("google_cloud_pubsub", dict(distributed_tracing_enabled=False)):
        with tracer.trace("parent.span"):
            future = publisher.publish(topic_path, b"Hello World")
            future.result(timeout=10)

    response = subscriber.pull(subscription=subscription_path, max_messages=1, timeout=10)
    assert len(response.received_messages) == 1
    assert not any(key in dict(response.received_messages[0].message.attributes) for key in TRACE_CONTEXT_KEYS)


@pytest.mark.snapshot(ignores=SNAPSHOT_IGNORES + ["meta.tracestate"])
def test_subscribe_receive(publisher, topic_path, subscriber, subscription_path):
    """Test that subscribing and receiving a message creates a consumer span with child spans."""
    received = threading.Event()

    def callback(message):
        with tracer.trace("subscriber.span"):
            message.ack()
        received.set()

    future = subscriber.subscribe(subscription_path, callback=callback)
    try:
        with tracer.trace("producer.span"):
            publisher.publish(topic_path, b"Hello World").result(timeout=10)
        assert received.wait(timeout=10), "Timed out waiting for message"
    finally:
        future.cancel()
        future.result(timeout=5)


def test_subscribe_span_link(publisher, topic_path, subscriber, subscription_path, test_spans):
    """Test that the consumer span has a span link to the producer trace context and is reparented by default."""
    received = threading.Event()

    def callback(message):
        message.ack()
        received.set()

    future = subscriber.subscribe(subscription_path, callback=callback)
    try:
        with tracer.trace("producer.span"):
            publisher.publish(topic_path, b"Hello World").result(timeout=10)
        assert received.wait(timeout=10), "Timed out waiting for message"
    finally:
        future.cancel()
        future.result(timeout=5)

    spans = test_spans.get_spans()
    consumer_spans = [s for s in spans if "receive" in s.name]
    assert len(consumer_spans) == 1
    consumer_span = consumer_spans[0]
    assert len(consumer_span._links) == 1

    producer_spans = [s for s in spans if "send" in s.name]
    assert len(producer_spans) == 1
    producer_span = producer_spans[0]
    link = consumer_span._links[0]
    assert link.trace_id == producer_span.trace_id
    assert link.span_id == producer_span.span_id

    # Reparented: consumer is in the same trace as the producer
    assert consumer_span.trace_id == producer_span.trace_id
    assert consumer_span.parent_id == producer_span.span_id


@pytest.mark.snapshot(ignores=SNAPSHOT_IGNORES + ["meta.tracestate"])
def test_subscribe_reparent_enabled_snapshot(publisher, topic_path, subscriber, subscription_path):
    """Snapshot test: receive span is reparented into the producer's trace (default behavior)."""
    received = threading.Event()

    def callback(message):
        with tracer.trace("subscriber.span"):
            message.ack()
        received.set()

    future = subscriber.subscribe(subscription_path, callback=callback)
    try:
        with tracer.trace("producer.span"):
            publisher.publish(topic_path, b"Hello World").result(timeout=10)
        assert received.wait(timeout=10), "Timed out waiting for message"
    finally:
        future.cancel()
        future.result(timeout=5)


@pytest.mark.snapshot(ignores=SNAPSHOT_IGNORES + ["meta.tracestate"])
def test_subscribe_reparent_disabled_snapshot(publisher, topic_path, subscriber, subscription_path):
    """Snapshot test: receive span is in a separate trace when reparenting is disabled."""
    received = threading.Event()

    def callback(message):
        with tracer.trace("subscriber.span"):
            message.ack()
        received.set()

    with override_config("google_cloud_pubsub", dict(reparent_enabled=False)):
        future = subscriber.subscribe(subscription_path, callback=callback)
        try:
            with tracer.trace("producer.span"):
                publisher.publish(topic_path, b"Hello World").result(timeout=10)
            assert received.wait(timeout=10), "Timed out waiting for message"
        finally:
            future.cancel()
            future.result(timeout=5)


def test_subscribe_span_tags(publisher, topic_path, subscriber, subscription_path, test_spans):
    """Test that the consumer span has all expected tags."""
    received = threading.Event()

    def callback(message):
        message.ack()
        received.set()

    future = subscriber.subscribe(subscription_path, callback=callback)
    try:
        publisher.publish(topic_path, b"Hello World").result(timeout=10)
        assert received.wait(timeout=10), "Timed out waiting for message"
    finally:
        future.cancel()
        future.result(timeout=5)

    spans = test_spans.get_spans()
    consumer_spans = [s for s in spans if "receive" in s.name]
    assert len(consumer_spans) == 1
    span = consumer_spans[0]
    assert span.get_tag("component") == "google_cloud_pubsub"
    assert span.get_tag("span.kind") == "consumer"
    assert span.get_tag("messaging.system") == "pubsub"
    assert span.get_tag("messaging.destination.name") == "test-subscription"
    assert span.get_tag("messaging.operation") == "receive"
    assert span.get_tag("gcloud.project_id") == "test-project"
    assert span.get_tag("messaging.message_id") is not None


def test_subscribe_propagation_disabled(publisher, topic_path, subscriber, subscription_path, test_spans):
    """Test that no span links are created when propagation is disabled, but span still exists."""
    received = threading.Event()

    def callback(message):
        message.ack()
        received.set()

    with override_config("google_cloud_pubsub", dict(distributed_tracing_enabled=False)):
        future = subscriber.subscribe(subscription_path, callback=callback)
        try:
            with tracer.trace("producer.span"):
                publisher.publish(topic_path, b"Hello World").result(timeout=10)
            assert received.wait(timeout=10), "Timed out waiting for message"
        finally:
            future.cancel()
            future.result(timeout=5)

    spans = test_spans.get_spans()
    consumer_spans = [s for s in spans if "receive" in s.name]
    assert len(consumer_spans) == 1
    assert len(consumer_spans[0]._links) == 0


def test_subscribe_reparent_disabled(publisher, topic_path, subscriber, subscription_path, test_spans):
    """Test that when reparent is disabled, receive span is in a separate trace with span links."""
    received = threading.Event()

    def callback(message):
        message.ack()
        received.set()

    with override_config("google_cloud_pubsub", dict(reparent_enabled=False)):
        future = subscriber.subscribe(subscription_path, callback=callback)
        try:
            with tracer.trace("producer.span"):
                publisher.publish(topic_path, b"Hello World").result(timeout=10)
            assert received.wait(timeout=10), "Timed out waiting for message"
        finally:
            future.cancel()
            future.result(timeout=5)

    spans = test_spans.get_spans()
    consumer_spans = [s for s in spans if "receive" in s.name]
    assert len(consumer_spans) == 1
    consumer_span = consumer_spans[0]

    producer_spans = [s for s in spans if "send" in s.name]
    assert len(producer_spans) == 1
    producer_span = producer_spans[0]

    # Not reparented: consumer is in a different trace
    assert consumer_span.trace_id != producer_span.trace_id

    # Span link still exists
    assert len(consumer_span._links) == 1
    link = consumer_span._links[0]
    assert link.trace_id == producer_span.trace_id
    assert link.span_id == producer_span.span_id


def test_subscribe_reparent_enabled(publisher, topic_path, subscriber, subscription_path, test_spans):
    """Test that when reparent is enabled, receive span is in the same trace as the producer."""
    received = threading.Event()

    def callback(message):
        message.ack()
        received.set()

    future = subscriber.subscribe(subscription_path, callback=callback)
    try:
        with tracer.trace("producer.span"):
            publisher.publish(topic_path, b"Hello World").result(timeout=10)
        assert received.wait(timeout=10), "Timed out waiting for message"
    finally:
        future.cancel()
        future.result(timeout=5)

    spans = test_spans.get_spans()
    consumer_spans = [s for s in spans if "receive" in s.name]
    assert len(consumer_spans) == 1
    consumer_span = consumer_spans[0]

    producer_spans = [s for s in spans if "send" in s.name]
    assert len(producer_spans) == 1
    producer_span = producer_spans[0]

    # Reparented: consumer is in the same trace
    assert consumer_span.trace_id == producer_span.trace_id
    assert consumer_span.parent_id == producer_span.span_id

    # Span link also exists
    assert len(consumer_span._links) == 1
    link = consumer_span._links[0]
    assert link.trace_id == producer_span.trace_id
    assert link.span_id == producer_span.span_id
