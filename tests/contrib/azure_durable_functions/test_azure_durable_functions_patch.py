import unittest

from ddtrace.contrib.internal.azure_functions.patch import patch as azure_functions_patch
from ddtrace.contrib.internal.azure_functions.patch import unpatch as azure_functions_unpatch
from tests.contrib.patch import PatchMixin


class TestAzureDurableFunctionsPatch(PatchMixin, unittest.TestCase):
    def test_dfapp_get_functions_wrapped(self):
        from azure.durable_functions.decorators import durable_app

        azure_functions_unpatch()
        self.assert_not_wrapped(durable_app.DFApp.get_functions)

        azure_functions_patch()
        try:
            self.assert_wrapped(durable_app.DFApp.get_functions)
        finally:
            azure_functions_unpatch()
            self.assert_not_wrapped(durable_app.DFApp.get_functions)
