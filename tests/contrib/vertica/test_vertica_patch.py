from ddtrace.contrib.vertica import patch
# from ddtrace.contrib.vertica import unpatch
from tests.contrib.patch import PatchTestCase


class TestVerticaPatch(PatchTestCase.Base):
    __integration_name__ = "vertica"
    __module_name__ = "vertica_python"
    __patch_func__ = patch
    # __unpatch_func__ = unpatch

    def assert_module_patched(self, vertica_python):
        pass

    def assert_not_module_patched(self, vertica_python):
        pass

    def assert_not_module_double_patched(self, vertica_python):
        pass

