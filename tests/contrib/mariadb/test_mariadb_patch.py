from ddtrace.contrib.mariadb import patch
# from ddtrace.contrib.mariadb import unpatch
from tests.contrib.patch import PatchTestCase


class TestMariadbPatch(PatchTestCase.Base):
    __integration_name__ = "mariadb"
    __module_name__ = "mariadb"
    __patch_func__ = patch
    # __unpatch_func__ = unpatch

    def assert_module_patched(self, mariadb):
        pass

    def assert_not_module_patched(self, mariadb):
        pass

    def assert_not_module_double_patched(self, mariadb):
        pass

