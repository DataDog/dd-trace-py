from ddtrace import config
from ddtrace.contrib.internal.trio.patch import get_version
from ddtrace.contrib.internal.trio.patch import patch
from ddtrace.contrib.internal.trio.patch import unpatch
from ddtrace.internal._context_watcher import context_switches_require_fallback
from tests.contrib.patch import PatchTestCase


def test_config_available_before_patch():
    """Trio configuration is available before integration patching."""
    assert config.trio is not None


class TestTrioPatch(PatchTestCase.Base):
    __integration_name__ = "trio"
    __module_name__ = "trio"
    __patch_func__ = patch
    __unpatch_func__ = unpatch
    __get_version__ = get_version

    def assert_module_patched(self, module):
        functions = (
            module.run,
            module.lowlevel.start_guest_run,
            module.to_thread.run_sync,
            module.from_thread.run_sync,
        )
        for function in functions:
            if context_switches_require_fallback():
                self.assert_wrapped(function)
            else:
                self.assert_not_wrapped(function)

    def assert_not_module_patched(self, module):
        self.assert_not_wrapped(module.run)
        self.assert_not_wrapped(module.lowlevel.start_guest_run)
        self.assert_not_wrapped(module.to_thread.run_sync)
        self.assert_not_wrapped(module.from_thread.run_sync)

    def assert_not_module_double_patched(self, module):
        functions = (
            module.run,
            module.lowlevel.start_guest_run,
            module.to_thread.run_sync,
            module.from_thread.run_sync,
        )
        for function in functions:
            if context_switches_require_fallback():
                self.assert_not_double_wrapped(function)
            else:
                self.assert_not_wrapped(function)
