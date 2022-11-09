from ddtrace.contrib.mongoengine import patch
# from ddtrace.contrib.mongoengine import unpatch
from tests.contrib.patch import PatchTestCase


class TestMongoenginePatch(PatchTestCase.Base):
    __integration_name__ = "mongoengine"
    __module_name__ = "mongoengine"
    __patch_func__ = patch
    # __unpatch_func__ = unpatch

    def assert_module_patched(self, mongoengine):
        pass

    def assert_not_module_patched(self, mongoengine):
        pass

    def assert_not_module_double_patched(self, mongoengine):
        pass

