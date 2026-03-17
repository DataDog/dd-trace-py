import threading

import pytest

from ddtrace.trace import tracer
from tests.utils import override_config


SNAPSHOT_IGNORES = [
    "meta._dd.tags.process",
    "meta.messaging.message_id",
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


@pytest.mark.snapshot(ignores=SNAPSHOT_IGNORES + ["meta.error.stack"])
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
    """Test that trace context keys are injected and user-provided attributes are preserved."""
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
