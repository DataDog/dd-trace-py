from ddtrace.contrib.pynamodb import patch
# from ddtrace.contrib.pynamodb import unpatch
from tests.contrib.patch import PatchTestCase


class TestPynamodbPatch(PatchTestCase.Base):
    __integration_name__ = "pynamodb"
    __module_name__ = "pynamodb.connection.base"
    __patch_func__ = patch
    # __unpatch_func__ = unpatch

    def assert_module_patched(self, pynamodb_connection_base):
        pass

    def assert_not_module_patched(self, pynamodb_connection_base):
        pass

    def assert_not_module_double_patched(self, pynamodb_connection_base):
        pass

