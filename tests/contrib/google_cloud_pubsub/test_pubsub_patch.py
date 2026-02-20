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
        from google.cloud.pubsub_v1.publisher.client import Client

        self.assert_wrapped(Client.publish)

    def assert_not_module_patched(self, pubsub_v1):
        from google.cloud.pubsub_v1.publisher.client import Client

        self.assert_not_wrapped(Client.publish)

    def assert_not_module_double_patched(self, pubsub_v1):
        from google.cloud.pubsub_v1.publisher.client import Client

        self.assert_not_double_wrapped(Client.publish)
