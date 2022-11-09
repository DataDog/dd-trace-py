from ddtrace.contrib.gevent import patch
# from ddtrace.contrib.gevent import unpatch
from tests.contrib.patch import PatchTestCase


class TestGeventPatch(PatchTestCase.Base):
    __integration_name__ = "gevent"
    __module_name__ = "gevent"
    __patch_func__ = patch
    # __unpatch_func__ = unpatch

    def assert_module_patched(self, gevent):
        pass

    def assert_not_module_patched(self, gevent):
        pass

    def assert_not_module_double_patched(self, gevent):
        pass

