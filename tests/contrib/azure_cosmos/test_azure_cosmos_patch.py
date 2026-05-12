from ddtrace.contrib.internal.azure_cosmos.patch import get_version
from ddtrace.contrib.internal.azure_cosmos.patch import patch
from tests.contrib.patch import PatchTestCase


try:
    from ddtrace.contrib.internal.azure_cosmos.patch import unpatch
except ImportError:
    unpatch = None


class TestAzureCosmosPatch(PatchTestCase.Base):
    __integration_name__ = "azure_cosmos"
    __module_name__ = "azure.cosmos"
    __patch_func__ = patch
    __unpatch_func__ = unpatch
    __get_version__ = get_version

    def assert_module_patched(self, azure_cosmos):
        self.assert_wrapped(azure_cosmos._synchronized_request.SynchronizedRequest)

    def assert_not_module_patched(self, azure_cosmos):
        self.assert_not_wrapped(azure_cosmos._synchronized_request.SynchronizedRequest)

    def assert_not_module_double_patched(self, azure_cosmos):
        self.assert_not_double_wrapped(azure_cosmos._synchronized_request.SynchronizedRequest)


class TestAzureCosmosAioPatch(PatchTestCase.Base):
    __integration_name__ = "azure_cosmos"
    __module_name__ = "azure.cosmos.aio"
    __patch_func__ = patch
    __unpatch_func__ = unpatch
    __get_version__ = get_version

    def assert_module_patched(self, azure_cosmos):
        self.assert_wrapped(azure_cosmos._asynchronous_request.AsynchronousRequest)

    def assert_not_module_patched(self, azure_cosmos):
        self.assert_not_wrapped(azure_cosmos._asynchronous_request.AsynchronousRequest)

    def assert_not_module_double_patched(self, azure_cosmos):
        self.assert_not_double_wrapped(azure_cosmos._asynchronous_request.AsynchronousRequest)
