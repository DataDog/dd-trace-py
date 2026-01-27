from ddtrace.contrib.internal.azure_functions.patch import get_version
from ddtrace.contrib.internal.azure_functions.patch import patch


try:
    from ddtrace.contrib.internal.azure_functions.patch import unpatch
except ImportError:
    unpatch = None
from tests.contrib.patch import PatchTestCase


class TestAzureFunctionsPatch(PatchTestCase.Base):
    __integration_name__ = "azure_functions"
    __module_name__ = "azure.functions"
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

    def assert_module_patched(self, azure_functions):
        self.assert_wrapped(azure_functions.FunctionApp.get_functions)
        dfapp = self._get_dfapp()
        if dfapp is not None:
            self.assert_wrapped(dfapp.get_functions)

    def assert_not_module_patched(self, azure_functions):
        self.assert_not_wrapped(azure_functions.FunctionApp.get_functions)
        dfapp = self._get_dfapp()
        if dfapp is not None:
            self.assert_not_wrapped(dfapp.get_functions)

    def assert_not_module_double_patched(self, azure_functions):
        self.assert_not_double_wrapped(azure_functions.FunctionApp.get_functions)
        dfapp = self._get_dfapp()
        if dfapp is not None:
            self.assert_not_double_wrapped(dfapp.get_functions)
