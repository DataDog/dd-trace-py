from ddtrace.contrib.subprocess.patch import patch


try:
    from ddtrace.contrib.subprocess.patch import unpatch
except ImportError:
    unpatch = None
from tests.contrib.patch import PatchTestCase


class TestSubprocessPatch(PatchTestCase.Base):
    __integration_name__ = "subprocess"
    __module_name__ = "subprocess"
    __patch_func__ = patch
    __unpatch_func__ = unpatch

    def assert_module_patched(self, subprocess):
        self.assert_wrapped(subprocess.Popen.__init__)
        self.assert_wrapped(subprocess.Popen.wait)

    def assert_not_module_patched(self, subprocess):
        self.assert_not_wrapped(subprocess.Popen.__init__)
        self.assert_not_wrapped(subprocess.Popen.wait)

    def assert_not_module_double_patched(self, subprocess):
        self.assert_not_double_wrapped(subprocess.Popen.__init__)
        self.assert_not_double_wrapped(subprocess.Popen.wait)
