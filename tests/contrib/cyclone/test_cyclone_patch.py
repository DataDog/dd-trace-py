from ddtrace.contrib.cyclone import patch
from tests.contrib.patch import PatchTestCase


class TestCylconePatch(PatchTestCase.Base):
    __integration_name__ = "cyclone"
    __module_name__ = "cyclone"
    __patch_func__ = patch
    __unpatch_func__ = None

    def assert_module_patched(self, cyclone):
        self.assert_wrapped(cyclone.web.RequestHandler._execute)
        self.assert_wrapped(cyclone.web.RequestHandler.on_finish)

    def assert_not_module_patched(self, cyclone):
        self.assert_not_wrapped(cyclone.web.RequestHandler._execute)
        self.assert_not_wrapped(cyclone.web.RequestHandler.on_finish)

    def assert_not_module_double_patched(self, cyclone):
        self.assert_not_double_wrapped(cyclone.web.RequestHanddouble_ler._execute)
        self.assert_not_double_wrapped(cyclone.web.RequestHandler.on_finish)
