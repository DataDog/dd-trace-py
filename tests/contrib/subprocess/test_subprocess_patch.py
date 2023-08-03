from ddtrace import config
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

    def __init__(self, *args, **kwargs):
        config._appsec_enabled = True
        super(TestSubprocessPatch, self).__init__(*args, **kwargs)

    def assert_module_patched(self, subprocess):
        self.assert_wrapped(subprocess.Popen.__init__)
        self.assert_wrapped(subprocess.Popen.wait)

    def assert_not_module_patched(self, subprocess):
        self.assert_not_wrapped(subprocess.Popen.__init__)
        self.assert_not_wrapped(subprocess.Popen.wait)

    def assert_not_module_double_patched(self, subprocess):
        self.assert_not_double_wrapped(subprocess.Popen.__init__)
        self.assert_not_double_wrapped(subprocess.Popen.wait)

    # These are disabled because the base class uses @run_in_subprocess which
    # import subprocess before we have a chance to patch. However, the contrib
    # unittests already test patch and unpatch
    def test_ddtrace_run_patch_on_import(self):
        pass

    def test_import_patch_unpatch_unpatch(self):
        pass

    def test_import_unpatch_patch(self):
        pass

    def test_patch_unpatch_import_unpatch(self):
        pass

    def test_patch_unpatch_unpatch_import(self):
        pass

    def test_unpatch_patch_import(self):
        pass
