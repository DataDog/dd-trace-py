from ddtrace.contrib.pylons import patch
# from ddtrace.contrib.pylons import unpatch
from tests.contrib.patch import PatchTestCase


class TestPylonsPatch(PatchTestCase.Base):
    __integration_name__ = "pylons"
    __module_name__ = "pylons.wsgiapp"
    __patch_func__ = patch
    # __unpatch_func__ = unpatch

    def assert_module_patched(self, pylons_wsgiapp):
        pass

    def assert_not_module_patched(self, pylons_wsgiapp):
        pass

    def assert_not_module_double_patched(self, pylons_wsgiapp):
        pass

