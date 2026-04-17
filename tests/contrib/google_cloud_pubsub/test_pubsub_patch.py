from google.cloud.pubsub_v1.publisher.client import Client as PublisherClient
from google.cloud.pubsub_v1.subscriber.client import Client as SubscriberClient
from google.pubsub_v1.services.publisher.client import PublisherClient as GapicPublisher
from google.pubsub_v1.services.schema_service.client import SchemaServiceClient
from google.pubsub_v1.services.subscriber.client import SubscriberClient as GapicSubscriber

from ddtrace.contrib.internal.google_cloud_pubsub.patch import get_version
from ddtrace.contrib.internal.google_cloud_pubsub.patch import patch
from ddtrace.contrib.internal.google_cloud_pubsub.patch import unpatch
from tests.contrib.patch import PatchTestCase


class TestGoogleCloudPubSubPatch(PatchTestCase.Base):
    __integration_name__ = "google_cloud_pubsub"
    __module_name__ = "google.cloud.pubsub_v1"
    __patch_func__ = patch
    __unpatch_func__ = unpatch
    __get_version__ = get_version

    def assert_module_patched(self, pubsub_v1):
        # Messaging operations
        self.assert_wrapped(PublisherClient.publish)
        self.assert_wrapped(SubscriberClient.subscribe)
        # Admin methods
        self.assert_wrapped(GapicPublisher.create_topic)
        self.assert_wrapped(GapicPublisher.delete_topic)
        self.assert_wrapped(GapicPublisher.get_topic)
        self.assert_wrapped(GapicPublisher.list_topics)
        self.assert_wrapped(GapicSubscriber.create_subscription)
        self.assert_wrapped(GapicSubscriber.delete_subscription)
        self.assert_wrapped(GapicSubscriber.get_subscription)
        self.assert_wrapped(GapicSubscriber.list_subscriptions)
        self.assert_wrapped(SchemaServiceClient.create_schema)
        self.assert_wrapped(SchemaServiceClient.delete_schema)

    def assert_not_module_patched(self, pubsub_v1):
        self.assert_not_wrapped(PublisherClient.publish)
        self.assert_not_wrapped(SubscriberClient.subscribe)
        self.assert_not_wrapped(GapicPublisher.create_topic)
        self.assert_not_wrapped(GapicSubscriber.create_subscription)
        self.assert_not_wrapped(SchemaServiceClient.create_schema)

    def assert_not_module_double_patched(self, pubsub_v1):
        self.assert_not_double_wrapped(PublisherClient.publish)
        self.assert_not_double_wrapped(SubscriberClient.subscribe)
        self.assert_not_double_wrapped(GapicPublisher.create_topic)
        self.assert_not_double_wrapped(GapicSubscriber.create_subscription)
        self.assert_not_double_wrapped(SchemaServiceClient.create_schema)
