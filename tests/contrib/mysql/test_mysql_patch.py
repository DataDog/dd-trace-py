from ddtrace.contrib.mysql import patch
# from ddtrace.contrib.mysql import unpatch
from tests.contrib.patch import PatchTestCase


class TestMysqlPatch(PatchTestCase.Base):
    __integration_name__ = "mysql"
    __module_name__ = "mysql.connector"
    __patch_func__ = patch
    # __unpatch_func__ = unpatch

    def assert_module_patched(self, mysql_connector):
        pass

    def assert_not_module_patched(self, mysql_connector):
        pass

    def assert_not_module_double_patched(self, mysql_connector):
        pass

