from ddtrace.contrib.asyncpg import patch
# from ddtrace.contrib.asyncpg import unpatch
from tests.contrib.patch import PatchTestCase


class TestAsyncpgPatch(PatchTestCase.Base):
    __integration_name__ = "asyncpg"
    __module_name__ = "asyncpg"
    __patch_func__ = patch
    # __unpatch_func__ = unpatch

    def assert_module_patched(self, asyncpg):
        pass

    def assert_not_module_patched(self, asyncpg):
        pass

    def assert_not_module_double_patched(self, asyncpg):
        pass

