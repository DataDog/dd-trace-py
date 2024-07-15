from ddtrace.contrib.anthropic import get_version
from ddtrace.contrib.anthropic import patch
from ddtrace.contrib.anthropic import unpatch
from tests.contrib.patch import PatchTestCase


class TestAnthropicPatch(PatchTestCase.Base):
    __integration_name__ = "anthropic"
    __module_name__ = "anthropic"
    __patch_func__ = patch
    __unpatch_func__ = unpatch
    __get_version__ = get_version

    def assert_module_patched(self, anthropic):
        self.assert_wrapped(anthropic.resources.messages.Messages.create)
        self.assert_wrapped(anthropic.resources.messages.AsyncMessages.create)

    def assert_not_module_patched(self, anthropic):
        self.assert_not_wrapped(anthropic.resources.messages.Messages.create)
        self.assert_not_wrapped(anthropic.resources.messages.AsyncMessages.create)

    def assert_not_module_double_patched(self, anthropic):
        self.assert_not_double_wrapped(anthropic.resources.messages.Messages.create)
        self.assert_not_double_wrapped(anthropic.resources.messages.AsyncMessages.create)
