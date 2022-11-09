from ddtrace.contrib.tornado import patch
# from ddtrace.contrib.tornado import unpatch
from tests.contrib.patch import PatchTestCase


class TestTornadoPatch(PatchTestCase.Base):
    __integration_name__ = "tornado"
    __module_name__ = "tornado"
    __patch_func__ = patch
    # __unpatch_func__ = unpatch

    def assert_module_patched(self, tornado):
        pass

    def assert_not_module_patched(self, tornado):
        pass

    def assert_not_module_double_patched(self, tornado):
        pass

