from ddtrace.contrib.redis import patch
# from ddtrace.contrib.redis import unpatch
from tests.contrib.patch import PatchTestCase


class TestRedisPatch(PatchTestCase.Base):
    __integration_name__ = "redis"
    __module_name__ = "redis"
    __patch_func__ = patch
    # __unpatch_func__ = unpatch

    def assert_module_patched(self, redis):
        pass

    def assert_not_module_patched(self, redis):
        pass

    def assert_not_module_double_patched(self, redis):
        pass

