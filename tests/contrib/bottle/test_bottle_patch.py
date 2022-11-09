from ddtrace.contrib.bottle import patch
# from ddtrace.contrib.bottle import unpatch
from tests.contrib.patch import PatchTestCase


class TestBottlePatch(PatchTestCase.Base):
    __integration_name__ = "bottle"
    __module_name__ = "bottle"
    __patch_func__ = patch
    # __unpatch_func__ = unpatch

    def assert_module_patched(self, bottle):
        pass

    def assert_not_module_patched(self, bottle):
        pass

    def assert_not_module_double_patched(self, bottle):
        pass

