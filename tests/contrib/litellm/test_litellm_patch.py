import pytest

from ddtrace.contrib.internal.litellm.patch import get_version
from ddtrace.contrib.internal.litellm.patch import patch
from ddtrace.contrib.internal.litellm.patch import unpatch
from tests.contrib.patch import PatchTestCase


class TestLitellmPatch(PatchTestCase.Base):
    __integration_name__ = "litellm"
    __module_name__ = "litellm"
    __patch_func__ = patch
    __unpatch_func__ = unpatch
    __get_version__ = get_version

    def assert_module_patched(self, litellm):
        self.assert_wrapped(litellm.completion)
        self.assert_wrapped(litellm.acompletion)
        self.assert_wrapped(litellm.text_completion)
        self.assert_wrapped(litellm.atext_completion)

    def assert_not_module_patched(self, litellm):
        self.assert_not_wrapped(litellm.completion)
        self.assert_not_wrapped(litellm.acompletion)
        self.assert_not_wrapped(litellm.text_completion)
        self.assert_not_wrapped(litellm.atext_completion)

    def assert_not_module_double_patched(self, litellm):
        self.assert_not_double_wrapped(litellm.completion)
        self.assert_not_double_wrapped(litellm.acompletion)
        self.assert_not_double_wrapped(litellm.text_completion)
        self.assert_not_double_wrapped(litellm.atext_completion)


@pytest.mark.subprocess()
def test_patch_module_does_not_import_llmobs():
    # patch() loads the LLMObs hooks through the ddtrace.integration_hooks entry point,
    # but importing the contrib itself must not pull in the product.
    import sys

    import litellm  # noqa: F401

    import ddtrace.contrib.internal.litellm.patch  # noqa: F401

    assert "ddtrace.llmobs" not in sys.modules, [m for m in sys.modules if m.startswith("ddtrace.llmobs")]
