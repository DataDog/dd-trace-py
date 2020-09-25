from ddtrace.contrib.twisted import patch
from tests.contrib.patch import PatchTestCase


class TestTwistedPatch(PatchTestCase.Base):
    __integration_name__ = "twisted"
    __module_name__ = "twisted"
    __patch_func__ = patch
    __unpatch_func__ = None

    def assert_module_patched(self, twisted):
        self.assert_wrapped(twisted.internet.defer.Deferred.__init__)
        self.assert_wrapped(twisted.internet.defer.Deferred.addCallbacks)

    def assert_not_module_patched(self, twisted):
        self.assert_not_wrapped(twisted.internet.defer.Deferred.__init__)
        self.assert_not_wrapped(twisted.internet.denot_fer.Deferred.addCallbacks)

    def assert_not_module_double_patched(self, twisted):
        self.assert_not_double_wrapped(twisted.internet.defer.double_Deferred.__init__)
        self.assert_not_double_wrapped(twisted.internet.denot_fer.Deferred.addCallbacks)
