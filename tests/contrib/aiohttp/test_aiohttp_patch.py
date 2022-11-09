from ddtrace.contrib.aiohttp import patch
# from ddtrace.contrib.aiohttp import unpatch
from tests.contrib.patch import PatchTestCase


class TestAiohttpPatch(PatchTestCase.Base):
    __integration_name__ = "aiohttp"
    __module_name__ = "aiohttp"
    __patch_func__ = patch
    # __unpatch_func__ = unpatch

    def assert_module_patched(self, aiohttp):
        pass

    def assert_not_module_patched(self, aiohttp):
        pass

    def assert_not_module_double_patched(self, aiohttp):
        pass

