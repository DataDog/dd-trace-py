from ddtrace.contrib.aiopg import patch
# from ddtrace.contrib.aiopg import unpatch
from tests.contrib.patch import PatchTestCase


class TestAiopgPatch(PatchTestCase.Base):
    __integration_name__ = "aiopg"
    __module_name__ = "aiopg"
    __patch_func__ = patch
    # __unpatch_func__ = unpatch

    def assert_module_patched(self, aiopg):
        pass

    def assert_not_module_patched(self, aiopg):
        pass

    def assert_not_module_double_patched(self, aiopg):
        pass

