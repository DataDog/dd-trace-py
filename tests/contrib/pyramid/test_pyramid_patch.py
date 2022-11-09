from ddtrace.contrib.pyramid import patch
# from ddtrace.contrib.pyramid import unpatch
from tests.contrib.patch import PatchTestCase


class TestPyramidPatch(PatchTestCase.Base):
    __integration_name__ = "pyramid"
    __module_name__ = "pyramid"
    __patch_func__ = patch
    # __unpatch_func__ = unpatch

    def assert_module_patched(self, pyramid):
        pass

    def assert_not_module_patched(self, pyramid):
        pass

    def assert_not_module_double_patched(self, pyramid):
        pass

