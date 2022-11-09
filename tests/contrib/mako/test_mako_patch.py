from ddtrace.contrib.mako import patch
# from ddtrace.contrib.mako import unpatch
from tests.contrib.patch import PatchTestCase


class TestMakoPatch(PatchTestCase.Base):
    __integration_name__ = "mako"
    __module_name__ = "mako"
    __patch_func__ = patch
    # __unpatch_func__ = unpatch

    def assert_module_patched(self, mako):
        pass

    def assert_not_module_patched(self, mako):
        pass

    def assert_not_module_double_patched(self, mako):
        pass

