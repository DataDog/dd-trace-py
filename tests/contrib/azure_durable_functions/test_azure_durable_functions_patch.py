from ddtrace.contrib.internal.azure_durable_functions.patch import get_version
from ddtrace.contrib.internal.azure_durable_functions.patch import patch


try:
    from ddtrace.contrib.internal.azure_durable_functions.patch import unpatch
except ImportError:
    unpatch = None
from tests.contrib.patch import PatchTestCase


class TestAzureDurableFunctionsPatch(PatchTestCase.Base):
    __integration_name__ = "azure_durable_functions"
    __module_name__ = "azure.durable_functions"
    __patch_func__ = patch
    __unpatch_func__ = unpatch
    __get_version__ = get_version

    @staticmethod
    def _get_dfapp():
        try:
            from azure.durable_functions.decorators import durable_app
        except Exception:
            return None
        return getattr(durable_app, "DFApp", None)

    def assert_module_patched(self, durable_functions):
        dfapp = self._get_dfapp()
        if dfapp is None:
            self.skipTest("DFApp not available in azure-functions-durable")
        self.assert_wrapped(dfapp.get_functions)

    def assert_not_module_patched(self, durable_functions):
        dfapp = self._get_dfapp()
        if dfapp is None:
            self.skipTest("DFApp not available in azure-functions-durable")
        self.assert_not_wrapped(dfapp.get_functions)

    def assert_not_module_double_patched(self, durable_functions):
        dfapp = self._get_dfapp()
        if dfapp is None:
            self.skipTest("DFApp not available in azure-functions-durable")
        self.assert_not_double_wrapped(dfapp.get_functions)
