from ddtrace.contrib.aredis import patch
# from ddtrace.contrib.aredis import unpatch
from tests.contrib.patch import PatchTestCase


class TestAredisPatch(PatchTestCase.Base):
    __integration_name__ = "aredis"
    __module_name__ = "aredis"
    __patch_func__ = patch
    # __unpatch_func__ = unpatch

    def assert_module_patched(self, aredis):
        pass

    def assert_not_module_patched(self, aredis):
        pass

    def assert_not_module_double_patched(self, aredis):
        pass

