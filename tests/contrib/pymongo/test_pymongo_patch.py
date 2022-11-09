from ddtrace.contrib.pymongo import patch
# from ddtrace.contrib.pymongo import unpatch
from tests.contrib.patch import PatchTestCase


class TestPymongoPatch(PatchTestCase.Base):
    __integration_name__ = "pymongo"
    __module_name__ = "pymongo"
    __patch_func__ = patch
    # __unpatch_func__ = unpatch

    def assert_module_patched(self, pymongo):
        pass

    def assert_not_module_patched(self, pymongo):
        pass

    def assert_not_module_double_patched(self, pymongo):
        pass

