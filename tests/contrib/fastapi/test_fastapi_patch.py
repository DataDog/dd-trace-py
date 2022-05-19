from ddtrace.contrib.fastapi import patch
from ddtrace.contrib.fastapi import unpatch
from tests.contrib.patch import PatchTestCase
from ddtrace import patch


class TestFastapiPatch(PatchTestCase.Base):
    __integration_name__ = "fastapi"
    __module_name__ = "fastapi"
    __patch_func__ = patch
    __unpatch_func__ = unpatch

    def assert_module_patched(self, fastapi):
        import fastapi.applications
        self.assert_wrapped(fastapi.applications.FastAPI.__init__)

    def assert_not_module_patched(self, fastapi):
        self.assert_not_wrapped(fastapi.__init__)

    def assert_not_module_double_patched(self, fastapi):
        self.assert_not_double_wrapped(fastapi.__init__)

    def  test_import_patch(self):
        import fastapi

        # patch(fastapi=True)
        self.assert_module_patched(fastapi)

