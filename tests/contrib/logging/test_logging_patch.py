from ddtrace.contrib.logging import patch
# from ddtrace.contrib.logging import unpatch
from tests.contrib.patch import PatchTestCase


class TestLoggingPatch(PatchTestCase.Base):
    __integration_name__ = "logging"
    __module_name__ = "logging"
    __patch_func__ = patch
    # __unpatch_func__ = unpatch

    def assert_module_patched(self, logging):
        pass

    def assert_not_module_patched(self, logging):
        pass

    def assert_not_module_double_patched(self, logging):
        pass

