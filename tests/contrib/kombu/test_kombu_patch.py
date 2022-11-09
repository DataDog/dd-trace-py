from ddtrace.contrib.kombu import patch
# from ddtrace.contrib.kombu import unpatch
from tests.contrib.patch import PatchTestCase


class TestKombuPatch(PatchTestCase.Base):
    __integration_name__ = "kombu"
    __module_name__ = "kombu"
    __patch_func__ = patch
    # __unpatch_func__ = unpatch

    def assert_module_patched(self, kombu):
        pass

    def assert_not_module_patched(self, kombu):
        pass

    def assert_not_module_double_patched(self, kombu):
        pass

