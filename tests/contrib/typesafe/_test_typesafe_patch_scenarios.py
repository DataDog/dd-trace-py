from ddtrace.contrib.internal.typesafe.patch import get_version
from ddtrace.contrib.internal.typesafe.patch import patch
from ddtrace.contrib.internal.typesafe.patch import unpatch
from tests.contrib.patch import PatchScenario


class TestTypeSafePatch(PatchScenario):
    __integration_name__ = "typesafe"
    __module_name__ = "typesafe_sdk"
    __patch_func__ = patch
    __unpatch_func__ = unpatch
    __get_version__ = get_version

    def assert_module_patched(self, typesafe_sdk):
        self.assert_wrapped(typesafe_sdk.TypeSafeClient.system_one)
        self.assert_wrapped(typesafe_sdk.AsyncTypeSafeClient.system_one)

    def assert_not_module_patched(self, typesafe_sdk):
        self.assert_not_wrapped(typesafe_sdk.TypeSafeClient.system_one)
        self.assert_not_wrapped(typesafe_sdk.AsyncTypeSafeClient.system_one)

    def assert_not_module_double_patched(self, typesafe_sdk):
        self.assert_not_double_wrapped(typesafe_sdk.TypeSafeClient.system_one)
        self.assert_not_double_wrapped(typesafe_sdk.AsyncTypeSafeClient.system_one)
