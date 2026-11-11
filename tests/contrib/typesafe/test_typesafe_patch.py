import pytest

from ddtrace.contrib.internal.typesafe.patch import get_version
from ddtrace.contrib.internal.typesafe.patch import patch
from ddtrace.contrib.internal.typesafe.patch import unpatch
from tests.contrib.patch import PatchTestCase


class TestTypeSafePatch(PatchTestCase.Base):
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


@pytest.mark.subprocess()
def test_patch_module_does_not_import_llmobs():
    # patch() loads the LLMObs hooks through the ddtrace.integration_hooks entry point,
    # but importing the contrib itself must not pull in the product.
    import sys

    import typesafe_sdk  # noqa: F401

    import ddtrace.contrib.internal.typesafe.patch  # noqa: F401

    assert "ddtrace.llmobs" not in sys.modules, [m for m in sys.modules if m.startswith("ddtrace.llmobs")]
