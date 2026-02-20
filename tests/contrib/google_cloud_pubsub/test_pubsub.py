import pytest

from ddtrace import config
from ddtrace.trace import tracer


SNAPSHOT_IGNORES = ["meta.runtime-id", "meta._dd.p.tid", "meta._dd.p.dm", "metrics.process_id"]

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
    original = config.google_cloud_pubsub.distributed_tracing_enabled
    config.google_cloud_pubsub.distributed_tracing_enabled = False
    try:
        with tracer.trace("parent.span"):
            future = publisher.publish(topic_path, b"Hello World")
            future.result(timeout=10)
    finally:
        config.google_cloud_pubsub.distributed_tracing_enabled = original

    response = subscriber.pull(subscription=subscription_path, max_messages=1, timeout=10)
    assert len(response.received_messages) == 1
    assert not any(key in dict(response.received_messages[0].message.attributes) for key in TRACE_CONTEXT_KEYS)
