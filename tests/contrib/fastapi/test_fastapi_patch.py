from ddtrace.contrib.fastapi import patch
from ddtrace.contrib.fastapi import unpatch
from tests.contrib.patch import PatchTestCase


class TestFastapiPatch(PatchTestCase.Base):
    __integration_name__ = "fastapi"
    __module_name__ = "fastapi"
    __patch_func__ = patch
    __unpatch_func__ = unpatch

    def assert_module_patched(self, fastapi):
        self.assert_wrapped(fastapi.applications.FastAPI.build_middleware_stack)
        self.assert_wrapped(fastapi.routing.serialize_response)
        self.assert_wrapped(fastapi.routing.APIRoute.handle)
        self.assert_wrapped(fastapi.routing.Mount.handle)

    def assert_not_module_patched(self, fastapi):
        self.assert_not_wrapped(fastapi.applications.FastAPI.build_middleware_stack)
        self.assert_not_wrapped(fastapi.routing.serialize_response)
        self.assert_not_wrapped(fastapi.routing.APIRoute.handle)
        self.assert_not_wrapped(fastapi.routing.Mount.handle)

    def assert_not_module_double_patched(self, fastapi):
        self.assert_not_double_wrapped(fastapi.applications.FastAPI.build_middleware_stack)
        self.assert_not_double_wrapped(fastapi.routing.serialize_response)
        self.assert_not_double_wrapped(fastapi.routing.APIRoute.handle)
        self.assert_not_double_wrapped(fastapi.routing.Mount.handle)
