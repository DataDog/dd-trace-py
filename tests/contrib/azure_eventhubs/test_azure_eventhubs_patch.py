from ddtrace.contrib.internal.azure_eventhubs.patch import get_version
from ddtrace.contrib.internal.azure_eventhubs.patch import patch


try:
    from ddtrace.contrib.internal.azure_eventhubs.patch import unpatch
except ImportError:
    unpatch = None
from tests.contrib.patch import PatchTestCase


class TestAzureEventHubsPatch(PatchTestCase.Base):
    __integration_name__ = "azure_eventhubs"
    __module_name__ = "azure.eventhub"
    __patch_func__ = patch
    __unpatch_func__ = unpatch
    __get_version__ = get_version

    def assert_module_patched(self, azure_eventhubs):
        self.assert_wrapped(azure_eventhubs.EventDataBatch.add)
        self.assert_wrapped(azure_eventhubs.EventHubProducerClient.__init__)
        self.assert_wrapped(azure_eventhubs.EventHubProducerClient.create_batch)
        self.assert_wrapped(azure_eventhubs.EventHubProducerClient.send_event)
        self.assert_wrapped(azure_eventhubs.EventHubProducerClient.send_batch)
        self.assert_wrapped(azure_eventhubs.EventHubProducerClient._buffered_send_event)
        self.assert_wrapped(azure_eventhubs.EventHubProducerClient._buffered_send_batch)

    def assert_not_module_patched(self, azure_eventhubs):
        self.assert_not_wrapped(azure_eventhubs.EventDataBatch.add)
        self.assert_not_wrapped(azure_eventhubs.EventHubProducerClient.__init__)
        self.assert_not_wrapped(azure_eventhubs.EventHubProducerClient.create_batch)
        self.assert_not_wrapped(azure_eventhubs.EventHubProducerClient.send_event)
        self.assert_not_wrapped(azure_eventhubs.EventHubProducerClient.send_batch)
        self.assert_not_wrapped(azure_eventhubs.EventHubProducerClient._buffered_send_event)
        self.assert_not_wrapped(azure_eventhubs.EventHubProducerClient._buffered_send_batch)

    def assert_not_module_double_patched(self, azure_eventhubs):
        self.assert_not_double_wrapped(azure_eventhubs.EventDataBatch.add)
        self.assert_not_double_wrapped(azure_eventhubs.EventHubProducerClient.__init__)
        self.assert_not_double_wrapped(azure_eventhubs.EventHubProducerClient.create_batch)
        self.assert_not_double_wrapped(azure_eventhubs.EventHubProducerClient.send_event)
        self.assert_not_double_wrapped(azure_eventhubs.EventHubProducerClient.send_batch)
        self.assert_not_double_wrapped(azure_eventhubs.EventHubProducerClient._buffered_send_event)
        self.assert_not_double_wrapped(azure_eventhubs.EventHubProducerClient._buffered_send_batch)


class TestAzureEventHubsAioPatch(PatchTestCase.Base):
    __integration_name__ = "azure_eventhubs"
    __module_name__ = "azure.eventhub.aio"
    __patch_func__ = patch
    __unpatch_func__ = unpatch
    __get_version__ = get_version

    def assert_module_patched(self, azure_eventhubs):
        self.assert_wrapped(azure_eventhubs.EventHubProducerClient.__init__)
        self.assert_wrapped(azure_eventhubs.EventHubProducerClient.create_batch)
        self.assert_wrapped(azure_eventhubs.EventHubProducerClient.send_event)
        self.assert_wrapped(azure_eventhubs.EventHubProducerClient.send_batch)
        self.assert_wrapped(azure_eventhubs.EventHubProducerClient._buffered_send_event)
        self.assert_wrapped(azure_eventhubs.EventHubProducerClient._buffered_send_batch)

    def assert_not_module_patched(self, azure_eventhubs):
        self.assert_not_wrapped(azure_eventhubs.EventHubProducerClient.__init__)
        self.assert_not_wrapped(azure_eventhubs.EventHubProducerClient.create_batch)
        self.assert_not_wrapped(azure_eventhubs.EventHubProducerClient.send_event)
        self.assert_not_wrapped(azure_eventhubs.EventHubProducerClient.send_batch)
        self.assert_not_wrapped(azure_eventhubs.EventHubProducerClient._buffered_send_event)
        self.assert_not_wrapped(azure_eventhubs.EventHubProducerClient._buffered_send_batch)

    def assert_not_module_double_patched(self, azure_eventhubs):
        self.assert_not_double_wrapped(azure_eventhubs.EventHubProducerClient.__init__)
        self.assert_not_double_wrapped(azure_eventhubs.EventHubProducerClient.create_batch)
        self.assert_not_double_wrapped(azure_eventhubs.EventHubProducerClient.send_event)
        self.assert_not_double_wrapped(azure_eventhubs.EventHubProducerClient.send_batch)
        self.assert_not_double_wrapped(azure_eventhubs.EventHubProducerClient._buffered_send_event)
        self.assert_not_double_wrapped(azure_eventhubs.EventHubProducerClient._buffered_send_batch)
