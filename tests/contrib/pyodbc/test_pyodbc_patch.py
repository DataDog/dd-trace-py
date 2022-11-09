from ddtrace.contrib.pyodbc import patch
# from ddtrace.contrib.pyodbc import unpatch
from tests.contrib.patch import PatchTestCase


class TestPyodbcPatch(PatchTestCase.Base):
    __integration_name__ = "pyodbc"
    __module_name__ = "pyodbc"
    __patch_func__ = patch
    # __unpatch_func__ = unpatch

    def assert_module_patched(self, pyodbc):
        pass

    def assert_not_module_patched(self, pyodbc):
        pass

    def assert_not_module_double_patched(self, pyodbc):
        pass

