from ddtrace.contrib.pymysql import patch
# from ddtrace.contrib.pymysql import unpatch
from tests.contrib.patch import PatchTestCase


class TestPymysqlPatch(PatchTestCase.Base):
    __integration_name__ = "pymysql"
    __module_name__ = "pymysql"
    __patch_func__ = patch
    # __unpatch_func__ = unpatch

    def assert_module_patched(self, pymysql):
        pass

    def assert_not_module_patched(self, pymysql):
        pass

    def assert_not_module_double_patched(self, pymysql):
        pass

