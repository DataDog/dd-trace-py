from ddtrace.contrib.dogpile_cache import patch
# from ddtrace.contrib.dogpile_cache import unpatch
from tests.contrib.patch import PatchTestCase


class TestDogpile_CachePatch(PatchTestCase.Base):
    __integration_name__ = "dogpile_cache"
    __module_name__ = "dogpile.cache"
    __patch_func__ = patch
    # __unpatch_func__ = unpatch

    def assert_module_patched(self, dogpile_cache):
        pass

    def assert_not_module_patched(self, dogpile_cache):
        pass

    def assert_not_module_double_patched(self, dogpile_cache):
        pass

