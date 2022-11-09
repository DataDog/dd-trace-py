from ddtrace.contrib.futures import patch
# from ddtrace.contrib.futures import unpatch
from tests.contrib.patch import PatchTestCase


class TestFuturesPatch(PatchTestCase.Base):
    __integration_name__ = "futures"
    __module_name__ = "concurrent.futures"
    __patch_func__ = patch
    # __unpatch_func__ = unpatch

    def assert_module_patched(self, concurrent_futures):
        pass

    def assert_not_module_patched(self, concurrent_futures):
        pass

    def assert_not_module_double_patched(self, concurrent_futures):
        pass

