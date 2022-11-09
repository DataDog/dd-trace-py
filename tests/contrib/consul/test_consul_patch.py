from ddtrace.contrib.consul import patch
# from ddtrace.contrib.consul import unpatch
from tests.contrib.patch import PatchTestCase


class TestConsulPatch(PatchTestCase.Base):
    __integration_name__ = "consul"
    __module_name__ = "consul"
    __patch_func__ = patch
    # __unpatch_func__ = unpatch

    def assert_module_patched(self, consul):
        pass

    def assert_not_module_patched(self, consul):
        pass

    def assert_not_module_double_patched(self, consul):
        pass

