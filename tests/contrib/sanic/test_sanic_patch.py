from ddtrace.contrib.sanic import patch
# from ddtrace.contrib.sanic import unpatch
from tests.contrib.patch import PatchTestCase


class TestSanicPatch(PatchTestCase.Base):
    __integration_name__ = "sanic"
    __module_name__ = "sanic"
    __patch_func__ = patch
    # __unpatch_func__ = unpatch

    def assert_module_patched(self, sanic):
        pass

    def assert_not_module_patched(self, sanic):
        pass

    def assert_not_module_double_patched(self, sanic):
        pass

