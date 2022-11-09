from ddtrace.contrib.falcon import patch
# from ddtrace.contrib.falcon import unpatch
from tests.contrib.patch import PatchTestCase


class TestFalconPatch(PatchTestCase.Base):
    __integration_name__ = "falcon"
    __module_name__ = "falcon"
    __patch_func__ = patch
    # __unpatch_func__ = unpatch

    def assert_module_patched(self, falcon):
        pass

    def assert_not_module_patched(self, falcon):
        pass

    def assert_not_module_double_patched(self, falcon):
        pass

