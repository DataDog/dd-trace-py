from ddtrace.contrib.httpx import patch
# from ddtrace.contrib.httpx import unpatch
from tests.contrib.patch import PatchTestCase


class TestHttpxPatch(PatchTestCase.Base):
    __integration_name__ = "httpx"
    __module_name__ = "httpx"
    __patch_func__ = patch
    # __unpatch_func__ = unpatch

    def assert_module_patched(self, httpx):
        pass

    def assert_not_module_patched(self, httpx):
        pass

    def assert_not_module_double_patched(self, httpx):
        pass

