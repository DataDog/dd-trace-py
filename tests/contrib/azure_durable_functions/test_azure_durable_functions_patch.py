from ddtrace.contrib.internal.azure_functions.patch import get_version
from ddtrace.contrib.internal.azure_functions.patch import patch
from ddtrace.contrib.internal.azure_functions.patch import unpatch
from tests.contrib.patch import PatchTestCase


class TestAzureFunctionsDurablePatch(PatchTestCase.Base):
    __integration_name__ = "azure_functions"
    __module_name__ = "azure.functions"
    __patch_func__ = patch
    __unpatch_func__ = unpatch
    __get_version__ = get_version

    @staticmethod
    def _get_dfapp():
        from azure.durable_functions.decorators import durable_app

        return durable_app.DFApp

    def assert_module_patched(self, azure_functions):
        self.assert_wrapped(self._get_dfapp().get_functions)

    def assert_not_module_patched(self, azure_functions):
        self.assert_not_wrapped(self._get_dfapp().get_functions)

    def assert_not_module_double_patched(self, azure_functions):
        self.assert_not_double_wrapped(self._get_dfapp().get_functions)
