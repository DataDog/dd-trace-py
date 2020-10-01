from ddtrace.contrib.django import patch
from tests.contrib.patch import PatchTestCase


class TestDjangoPatch(PatchTestCase.Base):
    __integration_name__ = "txredisapi"
    __module_name__ = "txredisapi"
    __patch_func__ = patch
    __unpatch_func__ = None

    def assert_module_patched(self, txredisapi):
        pass

    def assert_not_module_patched(self, txredisapi):
        pass

    def assert_not_module_double_patched(self, txredisapi):
        pass
