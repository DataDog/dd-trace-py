import threading

import pytest

from ddtrace.trace import tracer
from tests.utils import override_config


SNAPSHOT_IGNORES = [
    "meta._dd.tags.process",
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


@pytest.mark.snapshot(ignores=SNAPSHOT_IGNORES, wait_for_num_traces=3)
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


@pytest.mark.snapshot(ignores=SNAPSHOT_IGNORES + ["meta.tracestate"], wait_for_num_traces=5)
def test_subscribe_propagation_as_span_links_disabled(publisher, topic_path, subscriber, subscription_path):
    """Test publish-subscribe with propagation_as_span_links disabled (default). Validates span tags,
    trace hierarchy (receive is child of send), span links, and that child spans inside the callback
    are parented to the receive span.
    """
    received = threading.Event()

    def callback(message):
        with tracer.trace("subscriber.span"):
            message.ack()
        received.set()

    future = subscriber.subscribe(subscription_path, callback=callback)
    try:
        publisher.publish(topic_path, b"Hello World").result(timeout=10)
        assert received.wait(timeout=10), "Timed out waiting for message"
    finally:
        future.cancel()
        future.result(timeout=5)


@pytest.mark.snapshot(ignores=SNAPSHOT_IGNORES + ["meta.tracestate"], wait_for_num_traces=6)
def test_subscribe_propagation_as_span_links_enabled(publisher, topic_path, subscriber, subscription_path, test_spans):
    """Test publish-subscribe with propagation_as_span_links enabled. Validates that the receive span
    is in a separate trace from the producer and span links to the producer still exist.
    """
    received = threading.Event()

    def callback(message):
        message.ack()
        received.set()

    with override_config("google_cloud_pubsub", dict(propagation_as_span_links=True)):
        future = subscriber.subscribe(subscription_path, callback=callback)
        try:
            publisher.publish(topic_path, b"Hello World").result(timeout=10)
            assert received.wait(timeout=10), "Timed out waiting for message"
        finally:
            future.cancel()
            future.result(timeout=5)

    send_span = test_spans.find_span(name="gcp.pubsub.send")
    receive_span = test_spans.find_span(name="gcp.pubsub.receive")
    assert len(receive_span._get_links()) == 1
    link = receive_span._get_links()[0]
    assert link.trace_id == send_span.trace_id
    assert link.span_id == send_span.span_id


@pytest.mark.snapshot(ignores=SNAPSHOT_IGNORES + ["meta.error.stack", "meta.tracestate"], wait_for_num_traces=5)
def test_subscribe_callback_error(publisher, topic_path, subscriber, subscription_path):
    """Test that the receive span records error info when the user callback raises an exception."""
    received = threading.Event()

    def callback(message):
        message.ack()
        received.set()
        raise ValueError("callback error")

    future = subscriber.subscribe(subscription_path, callback=callback)
    try:
        publisher.publish(topic_path, b"Hello World").result(timeout=10)
        assert received.wait(timeout=10), "Timed out waiting for message"
    finally:
        future.cancel()
        try:
            future.result(timeout=5)
        except ValueError:
            pass


@pytest.mark.snapshot(ignores=SNAPSHOT_IGNORES + ["meta.tracestate"], wait_for_num_traces=6)
def test_subscribe_propagation_disabled(publisher, topic_path, subscriber, subscription_path, test_spans):
    """Test that when propagation is disabled, the receive span still exists but has no span
    links and is not reparented into the producer trace.
    """
    received = threading.Event()

    def callback(message):
        message.ack()
        received.set()

    with override_config("google_cloud_pubsub", dict(distributed_tracing_enabled=False)):
        future = subscriber.subscribe(subscription_path, callback=callback)
        try:
            publisher.publish(topic_path, b"Hello World").result(timeout=10)
            assert received.wait(timeout=10), "Timed out waiting for message"
        finally:
            future.cancel()
            future.result(timeout=5)

    receive_span = test_spans.find_span(name="gcp.pubsub.receive")
    assert len(receive_span._get_links()) == 0, "Receive span should have no span links when propagation is disabled"
