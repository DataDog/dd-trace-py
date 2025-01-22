from ddtrace.contrib.internal.starlette.patch import get_version
from ddtrace.contrib.internal.starlette.patch import patch
from ddtrace.contrib.internal.starlette.patch import unpatch
from tests.contrib.patch import PatchTestCase


class TestStarlettePatch(PatchTestCase.Base):
    __integration_name__ = "starlette"
    __module_name__ = "starlette"
    __patch_func__ = patch
    __unpatch_func__ = unpatch
    __get_version__ = get_version

    def assert_module_patched(self, starlette):
        self.assert_wrapped(starlette.applications.Starlette.__init__)
        self.assert_wrapped(starlette.routing.Mount.handle)
        self.assert_wrapped(starlette.routing.Route.handle)

    def assert_not_module_patched(self, starlette):
        import starlette.applications

        self.assert_not_wrapped(starlette.applications.Starlette.__init__)
        self.assert_not_wrapped(starlette.routing.Mount.handle)
        self.assert_not_wrapped(starlette.routing.Route.handle)

    def assert_not_module_double_patched(self, starlette):
        self.assert_not_double_wrapped(starlette.applications.Starlette.__init__)
        self.assert_not_double_wrapped(starlette.routing.Mount.handle)
        self.assert_not_double_wrapped(starlette.routing.Route.handle)
