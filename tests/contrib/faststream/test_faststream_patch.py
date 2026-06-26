from ddtrace.contrib.internal.faststream.patch import get_version
from ddtrace.contrib.internal.faststream.patch import patch
from ddtrace.contrib.internal.faststream.patch import unpatch
from tests.contrib.patch import PatchTestCase


class TestFastStreamPatch(PatchTestCase.Base):
    __integration_name__ = "faststream"
    __module_name__ = "faststream"
    __patch_func__ = patch
    __unpatch_func__ = unpatch
    __get_version__ = get_version

    def assert_module_patched(self, faststream):
        pass

    def assert_not_module_patched(self, faststream):
        pass

    def assert_not_module_double_patched(self, faststream):
        pass
