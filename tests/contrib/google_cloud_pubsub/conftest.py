from google.cloud import pubsub_v1
import pytest

from ddtrace.contrib.internal.google_cloud_pubsub.patch import patch
from ddtrace.contrib.internal.google_cloud_pubsub.patch import unpatch
from tests.contrib.config import PUBSUB_CONFIG


EMULATOR_HOST = "{host}:{port}".format(**PUBSUB_CONFIG)
PROJECT_ID = "test-project"
TOPIC_ID = "test-topic"
SUBSCRIPTION_ID = "test-subscription"


@pytest.fixture(autouse=True)
def pubsub_emulator_env(monkeypatch):
    """Point the google-cloud-pubsub library at the local emulator."""
    monkeypatch.setenv("PUBSUB_EMULATOR_HOST", EMULATOR_HOST)


@pytest.fixture(autouse=True)
def patch_pubsub():
    patch()
    yield
    unpatch()


@pytest.fixture
def publisher():
    client = pubsub_v1.PublisherClient()
    yield client
    client.transport.close()


@pytest.fixture
def batch_publisher():
    from google.cloud.pubsub_v1 import types

    client = pubsub_v1.PublisherClient(
        batch_settings=types.BatchSettings(max_messages=10, max_latency=0.5),
    )
    yield client
    client.transport.close()


@pytest.fixture
def topic_path(publisher):
    topic = publisher.create_topic(name=f"projects/{PROJECT_ID}/topics/{TOPIC_ID}")
    yield topic.name
    publisher.delete_topic(topic=topic.name)


@pytest.fixture
def subscriber():
    client = pubsub_v1.SubscriberClient()
    yield client
    client.close()


@pytest.fixture
def subscription_path(subscriber, topic_path):
    sub_path = subscriber.subscription_path(PROJECT_ID, SUBSCRIPTION_ID)
    subscriber.create_subscription(name=sub_path, topic=topic_path)
    yield sub_path
    subscriber.delete_subscription(subscription=sub_path)
