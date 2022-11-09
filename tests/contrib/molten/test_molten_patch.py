from ddtrace.contrib.molten import patch
# from ddtrace.contrib.molten import unpatch
from tests.contrib.patch import PatchTestCase


class TestMoltenPatch(PatchTestCase.Base):
    __integration_name__ = "molten"
    __module_name__ = "molten"
    __patch_func__ = patch
    # __unpatch_func__ = unpatch

    def assert_module_patched(self, molten):
        pass

    def assert_not_module_patched(self, molten):
        pass

    def assert_not_module_double_patched(self, molten):
        pass

