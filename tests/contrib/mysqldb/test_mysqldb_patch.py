from ddtrace.contrib.mysqldb import patch
# from ddtrace.contrib.mysqldb import unpatch
from tests.contrib.patch import PatchTestCase


class TestMysqldbPatch(PatchTestCase.Base):
    __integration_name__ = "mysqldb"
    __module_name__ = "MySQLdb"
    __patch_func__ = patch
    # __unpatch_func__ = unpatch

    def assert_module_patched(self, MySQLdb):
        pass

    def assert_not_module_patched(self, MySQLdb):
        pass

    def assert_not_module_double_patched(self, MySQLdb):
        pass

