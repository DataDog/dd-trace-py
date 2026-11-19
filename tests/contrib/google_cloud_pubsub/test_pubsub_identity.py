"""Tests for the gcp.pubsub.topic.name and gcp.pubsub.subscription.name identity tags."""

import threading

import pytest

from ddtrace.contrib.internal.google_cloud_pubsub.utils import subscription_path as validate_subscription_path
from ddtrace.contrib.internal.google_cloud_pubsub.utils import topic_path as validate_topic_path

from .conftest import PROJECT_ID


OTHER_PROJECT_ID = "other-project"


@pytest.mark.parametrize(
    "path,expected",
    [
        ("projects/p/topics/t", "projects/p/topics/t"),
        ("t", None),
        ("projects/p/topics/", None),
        ("projects//topics/t", None),
        ("projects/p/topics/t/extra", None),
        ("projects/p/subscriptions/s", None),
        ("_deleted-topic_", None),
        ("", None),
        (None, None),
    ],
)
def test_topic_path_validation(path, expected):
    assert validate_topic_path(path) == expected


@pytest.mark.parametrize(
    "path,expected",
    [
        ("projects/p/subscriptions/s", "projects/p/subscriptions/s"),
        ("s", None),
        ("projects/p/subscriptions/", None),
        ("projects/p/topics/t", None),
        ("", None),
        (None, None),
    ],
)
def test_subscription_path_validation(path, expected):
    assert validate_subscription_path(path) == expected


@pytest.fixture
def other_topic_path(publisher):
    topic = publisher.create_topic(name=f"projects/{PROJECT_ID}/topics/orders")
    yield topic.name
    publisher.delete_topic(topic=topic.name)


@pytest.fixture(params=[PROJECT_ID, OTHER_PROJECT_ID], ids=["same-project", "cross-project"])
def differently_named_subscription(request, subscriber, other_topic_path):
    sub_path = subscriber.subscription_path(request.param, "warehouse-orders")
    subscriber.create_subscription(name=sub_path, topic=other_topic_path)
    yield sub_path
    subscriber.delete_subscription(subscription=sub_path)


def test_publish_and_receive_identities(
    publisher, subscriber, other_topic_path, differently_named_subscription, test_spans
):
    received = threading.Event()

    def callback(message):
        message.ack()
        received.set()

    future = subscriber.subscribe(differently_named_subscription, callback=callback)
    try:
        publisher.publish(other_topic_path, b"order").result(timeout=10)
        assert received.wait(timeout=10), "Timed out waiting for message"
    finally:
        future.cancel()
        future.result(timeout=5)

    send_span = test_spans.find_span(name="gcp.pubsub.send")
    receive_span = test_spans.find_span(name="gcp.pubsub.receive")

    # Producer: the topic path from the publish request
    assert send_span.get_tag("gcp.pubsub.topic.name") == other_topic_path
    assert send_span.get_tag("gcp.pubsub.subscription.name") is None
    # Consumer: its own subscription path, with its own project; the topic is not known here and
    # is not derived from the subscription
    assert receive_span.get_tag("gcp.pubsub.subscription.name") == differently_named_subscription
    assert receive_span.get_tag("gcp.pubsub.topic.name") is None

    # Existing fields are unchanged
    subscription_project = differently_named_subscription.split("/")[1]
    assert send_span.get_tag("gcloud.project_id") == PROJECT_ID
    assert send_span.get_tag("messaging.destination.name") == "orders"
    assert send_span.resource == "orders"
    assert receive_span.get_tag("gcloud.project_id") == subscription_project
    assert receive_span.get_tag("messaging.destination.name") == "warehouse-orders"
    assert receive_span.resource == "warehouse-orders"

    # No metadata requests were issued to find the topic of the subscription
    methods = {span.get_tag("pubsub.method") for span in test_spans.filter_spans(name="gcp.pubsub.request")}
    assert not methods & {"getSubscription", "getTopic"}
