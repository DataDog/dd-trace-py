import starlette

from ddtrace.contrib.fastapi import patch
from ddtrace.contrib.fastapi import unpatch
from tests.contrib.patch import PatchTestCase


# Patching FastAPI also patches Starlette, so we test the Starlette methods as well
class TestFastapiPatch(PatchTestCase.Base):
    __integration_name__ = "fastapi"
    __module_name__ = "fastapi"
    __patch_func__ = patch
    __unpatch_func__ = unpatch

    def assert_module_patched(self, fastapi):
        self.assert_wrapped(fastapi.applications.FastAPI.__init__)
        self.assert_wrapped(fastapi.routing.serialize_response)

        self.assert_wrapped(starlette.applications.Starlette.__init__)
        self.assert_wrapped(starlette.routing.Mount.handle)
        self.assert_wrapped(starlette.routing.Route.handle)

    def assert_not_module_patched(self, fastapi):
        self.assert_not_wrapped(fastapi.applications.FastAPI.__init__)
        self.assert_not_wrapped(fastapi.routing.serialize_response)

        self.assert_not_wrapped(starlette.applications.Starlette.__init__)
        self.assert_not_wrapped(starlette.routing.Mount.handle)
        self.assert_not_wrapped(starlette.routing.Route.handle)

    def assert_not_module_double_patched(self, fastapi):
        self.assert_not_double_wrapped(fastapi.applications.FastAPI.__init__)
        self.assert_not_double_wrapped(fastapi.routing.serialize_response)

        self.assert_not_double_wrapped(starlette.applications.Starlette.__init__)
        self.assert_not_double_wrapped(starlette.routing.Mount.handle)
        self.assert_not_double_wrapped(starlette.routing.Route.handle)
