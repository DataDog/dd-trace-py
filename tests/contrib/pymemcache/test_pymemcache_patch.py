from ddtrace.contrib.pymemcache import patch
# from ddtrace.contrib.pymemcache import unpatch
from tests.contrib.patch import PatchTestCase


class TestPymemcachePatch(PatchTestCase.Base):
    __integration_name__ = "pymemcache"
    __module_name__ = "pymemcache"
    __patch_func__ = patch
    # __unpatch_func__ = unpatch

    def assert_module_patched(self, pymemcache):
        pass

    def assert_not_module_patched(self, pymemcache):
        pass

    def assert_not_module_double_patched(self, pymemcache):
        pass

