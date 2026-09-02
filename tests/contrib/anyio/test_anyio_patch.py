from ddtrace.contrib.internal.anyio.patch import get_version
from ddtrace.contrib.internal.anyio.patch import patch
from ddtrace.contrib.internal.anyio.patch import unpatch
from tests.contrib.patch import PatchTestCase


class TestAnyIOPatch(PatchTestCase.Base):
    __integration_name__ = "anyio"
    __module_name__ = "anyio"
    __patch_func__ = patch
    __unpatch_func__ = unpatch
    __get_version__ = get_version

    def assert_module_patched(self, module):
        assert module._datadog_patch

    def assert_not_module_patched(self, module):
        assert not getattr(module, "_datadog_patch", False)

    def assert_not_module_double_patched(self, module):
        assert module._datadog_patch
