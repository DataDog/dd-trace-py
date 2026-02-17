from ddtrace.contrib.internal.niquests.patch import get_version
from ddtrace.contrib.internal.niquests.patch import patch


try:
    from ddtrace.contrib.internal.niquests.patch import unpatch
except ImportError:
    unpatch = None

from tests.contrib.patch import PatchTestCase


class TestRequestsPatch(PatchTestCase.Base):
    __integration_name__ = "niquests"
    __module_name__ = "niquests"
    __patch_func__ = patch
    __unpatch_func__ = unpatch
    __get_version__ = get_version

    def assert_module_patched(self, requests):
        pass

    def assert_not_module_patched(self, requests):
        pass

    def assert_not_module_double_patched(self, requests):
        pass
