from ddtrace.contrib.internal.azure_durable_functions.patch import get_version
from ddtrace.contrib.internal.azure_durable_functions.patch import patch
from ddtrace.contrib.internal.azure_durable_functions.patch import unpatch
from tests.contrib.patch import PatchTestCase


class TestAzureDurableFunctionsPatch(PatchTestCase.Base):
    __integration_name__ = "azure_durable_functions"
    __module_name__ = "azure.durable_functions"
    __patch_func__ = patch
    __unpatch_func__ = unpatch
    __get_version__ = get_version

    @staticmethod
    def _get_dfapp():
        from azure.durable_functions.decorators import durable_app

        return durable_app.DFApp

    @staticmethod
    def _get_client():
        from azure.durable_functions.models.DurableOrchestrationClient import DurableOrchestrationClient

        return DurableOrchestrationClient

    def assert_module_patched(self, durable_functions):
        self.assert_wrapped(self._get_dfapp().get_functions)
        if hasattr(self._get_client(), "_get_current_activity_context"):
            self.assert_wrapped(self._get_client()._get_current_activity_context)

    def assert_not_module_patched(self, durable_functions):
        self.assert_not_wrapped(self._get_dfapp().get_functions)
        if hasattr(self._get_client(), "_get_current_activity_context"):
            self.assert_not_wrapped(self._get_client()._get_current_activity_context)

    def assert_not_module_double_patched(self, durable_functions):
        self.assert_not_double_wrapped(self._get_dfapp().get_functions)
        if hasattr(self._get_client(), "_get_current_activity_context"):
            self.assert_not_double_wrapped(self._get_client()._get_current_activity_context)
