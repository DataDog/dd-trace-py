from ddtrace.contrib.pylibmc import patch
# from ddtrace.contrib.pylibmc import unpatch
from tests.contrib.patch import PatchTestCase


class TestPylibmcPatch(PatchTestCase.Base):
    __integration_name__ = "pylibmc"
    __module_name__ = "pylibmc"
    __patch_func__ = patch
    # __unpatch_func__ = unpatch

    def assert_module_patched(self, pylibmc):
        pass

    def assert_not_module_patched(self, pylibmc):
        pass

    def assert_not_module_double_patched(self, pylibmc):
        pass

