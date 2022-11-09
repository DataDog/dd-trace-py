from ddtrace.contrib.aiomysql import patch
# from ddtrace.contrib.aiomysql import unpatch
from tests.contrib.patch import PatchTestCase


class TestAiomysqlPatch(PatchTestCase.Base):
    __integration_name__ = "aiomysql"
    __module_name__ = "aiomysql"
    __patch_func__ = patch
    # __unpatch_func__ = unpatch

    def assert_module_patched(self, aiomysql):
        pass

    def assert_not_module_patched(self, aiomysql):
        pass

    def assert_not_module_double_patched(self, aiomysql):
        pass

