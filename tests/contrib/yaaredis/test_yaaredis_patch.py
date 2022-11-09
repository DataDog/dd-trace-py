from ddtrace.contrib.yaaredis import patch
# from ddtrace.contrib.yaaredis import unpatch
from tests.contrib.patch import PatchTestCase


class TestYaaredisPatch(PatchTestCase.Base):
    __integration_name__ = "yaaredis"
    __module_name__ = "yaaredis"
    __patch_func__ = patch
    # __unpatch_func__ = unpatch

    def assert_module_patched(self, yaaredis):
        pass

    def assert_not_module_patched(self, yaaredis):
        pass

    def assert_not_module_double_patched(self, yaaredis):
        pass

