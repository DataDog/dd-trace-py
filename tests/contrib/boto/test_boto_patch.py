from ddtrace.contrib.boto import patch
# from ddtrace.contrib.boto import unpatch
from tests.contrib.patch import PatchTestCase


class TestBotoPatch(PatchTestCase.Base):
    __integration_name__ = "boto"
    __module_name__ = "boto.connection"
    __patch_func__ = patch
    # __unpatch_func__ = unpatch

    def assert_module_patched(self, boto_connection):
        pass

    def assert_not_module_patched(self, boto_connection):
        pass

    def assert_not_module_double_patched(self, boto_connection):
        pass

