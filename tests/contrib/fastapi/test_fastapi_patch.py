from ddtrace.contrib.fastapi import patch, unpatch
from tests.contrib.patch import PatchTestCase


class TestFastapiPatch(PatchTestCase.Base):
    __integration_name__ = "fastapi"
    __module_name__ = "fastapi"
    __patch_func__ = patch
    __unpatch_func__ = None

    def assert_module_patched(self, fastapi):
        self.assert_wrapped(fastapi.__init__)

    def assert_not_module_patched(self, fastapi):
        self.assert_not_wrapped(fastapi.__init__)

    def assert_not_module_double_patched(self, fastapi):
        self.assert_not_double_wrapped(fastapi.__init__)
