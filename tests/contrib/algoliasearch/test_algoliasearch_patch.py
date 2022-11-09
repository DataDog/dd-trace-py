from ddtrace.contrib.algoliasearch import patch
# from ddtrace.contrib.algoliasearch import unpatch
from tests.contrib.patch import PatchTestCase


class TestAlgoliasearchPatch(PatchTestCase.Base):
    __integration_name__ = "algoliasearch"
    __module_name__ = "algoliasearch"
    __patch_func__ = patch
    # __unpatch_func__ = unpatch

    def assert_module_patched(self, algoliasearch):
        pass

    def assert_not_module_patched(self, algoliasearch):
        pass

    def assert_not_module_double_patched(self, algoliasearch):
        pass

