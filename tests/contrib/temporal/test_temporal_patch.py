from ddtrace.contrib.internal.temporal.patch import get_version
from ddtrace.contrib.internal.temporal.patch import patch
from ddtrace.contrib.internal.temporal.patch import unpatch
from tests.contrib.patch import PatchTestCase


class TestTemporalPatch(PatchTestCase.Base):
    __integration_name__ = "temporal"
    __module_name__ = "temporalio"
    __patch_func__ = patch
    __unpatch_func__ = unpatch
    __get_version__ = get_version

    def assert_module_patched(self, temporalio):
        self.assert_wrapped(temporalio.client.Client.__init__)

    def assert_not_module_patched(self, temporalio):
        self.assert_not_wrapped(temporalio.client.Client.__init__)

    def assert_not_module_double_patched(self, temporalio):
        self.assert_not_double_wrapped(temporalio.client.Client.__init__)

    def assert_module_implements_get_version(self):
        self.assertTrue(callable(self.__get_version__))
        self.assertIsInstance(self.__get_version__(), str)
