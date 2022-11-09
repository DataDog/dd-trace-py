from ddtrace.contrib.aioredis import patch
# from ddtrace.contrib.aioredis import unpatch
from tests.contrib.patch import PatchTestCase


class TestAioredisPatch(PatchTestCase.Base):
    __integration_name__ = "aioredis"
    __module_name__ = "aioredis"
    __patch_func__ = patch
    # __unpatch_func__ = unpatch

    def assert_module_patched(self, aioredis):
        pass

    def assert_not_module_patched(self, aioredis):
        pass

    def assert_not_module_double_patched(self, aioredis):
        pass

