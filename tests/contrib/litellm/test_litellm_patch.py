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
