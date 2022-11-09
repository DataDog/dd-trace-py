from ddtrace.contrib.rediscluster import patch
# from ddtrace.contrib.rediscluster import unpatch
from tests.contrib.patch import PatchTestCase


class TestRedisclusterPatch(PatchTestCase.Base):
    __integration_name__ = "rediscluster"
    __module_name__ = "rediscluster"
    __patch_func__ = patch
    # __unpatch_func__ = unpatch

    def assert_module_patched(self, rediscluster):
        pass

    def assert_not_module_patched(self, rediscluster):
        pass

    def assert_not_module_double_patched(self, rediscluster):
        pass

