from ddtrace.contrib.internal.anyio.patch import get_version
from ddtrace.contrib.internal.anyio.patch import patch
from ddtrace.contrib.internal.anyio.patch import unpatch
from ddtrace.internal._context_watcher import context_switches_require_fallback
from tests.contrib.patch import PatchTestCase


class TestAnyIOPatch(PatchTestCase.Base):
    __integration_name__ = "anyio"
    __module_name__ = "anyio"
    __patch_func__ = patch
    __unpatch_func__ = unpatch
    __get_version__ = get_version

    def assert_module_patched(self, module):
        if context_switches_require_fallback():
            self.assert_wrapped(module.to_thread.run_sync)
        else:
            self.assert_not_wrapped(module.to_thread.run_sync)

    def assert_not_module_patched(self, module):
        self.assert_not_wrapped(module.to_thread.run_sync)

    def assert_not_module_double_patched(self, module):
        if context_switches_require_fallback():
            self.assert_not_double_wrapped(module.to_thread.run_sync)
        else:
            self.assert_not_wrapped(module.to_thread.run_sync)
