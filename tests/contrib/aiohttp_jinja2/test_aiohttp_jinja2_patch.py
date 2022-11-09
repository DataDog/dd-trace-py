from ddtrace.contrib.aiohttp_jinja2 import patch
# from ddtrace.contrib.aiohttp_jinja2 import unpatch
from tests.contrib.patch import PatchTestCase


class TestAiohttp_Jinja2Patch(PatchTestCase.Base):
    __integration_name__ = "aiohttp_jinja2"
    __module_name__ = "aiohttp_jinja2"
    __patch_func__ = patch
    # __unpatch_func__ = unpatch

    def assert_module_patched(self, aiohttp_jinja2):
        pass

    def assert_not_module_patched(self, aiohttp_jinja2):
        pass

    def assert_not_module_double_patched(self, aiohttp_jinja2):
        pass

