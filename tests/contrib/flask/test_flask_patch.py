from ddtrace.contrib.flask import patch
# from ddtrace.contrib.flask import unpatch
from tests.contrib.patch import PatchTestCase


class TestFlaskPatch(PatchTestCase.Base):
    __integration_name__ = "flask"
    __module_name__ = "flask"
    __patch_func__ = patch
    # __unpatch_func__ = unpatch

    def assert_module_patched(self, flask):
        pass

    def assert_not_module_patched(self, flask):
        pass

    def assert_not_module_double_patched(self, flask):
        pass

