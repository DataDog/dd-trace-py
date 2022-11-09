from ddtrace.contrib.requests import patch
# from ddtrace.contrib.requests import unpatch
from tests.contrib.patch import PatchTestCase


class TestRequestsPatch(PatchTestCase.Base):
    __integration_name__ = "requests"
    __module_name__ = "requests"
    __patch_func__ = patch
    # __unpatch_func__ = unpatch

    def assert_module_patched(self, requests):
        pass

    def assert_not_module_patched(self, requests):
        pass

    def assert_not_module_double_patched(self, requests):
        pass

