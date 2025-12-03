from ddtrace.contrib.internal.anthropic.patch import ANTHROPIC_VERSION
from ddtrace.contrib.internal.anthropic.patch import get_version
from ddtrace.contrib.internal.anthropic.patch import patch
from ddtrace.contrib.internal.anthropic.patch import unpatch
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
        if ANTHROPIC_VERSION >= (0, 37):
            self.assert_wrapped(anthropic.resources.beta.messages.messages.Messages.create)
            self.assert_wrapped(anthropic.resources.beta.messages.messages.AsyncMessages.create)

    def assert_not_module_patched(self, anthropic):
        self.assert_not_wrapped(anthropic.resources.messages.Messages.create)
        self.assert_not_wrapped(anthropic.resources.messages.AsyncMessages.create)
        if ANTHROPIC_VERSION >= (0, 37):
            self.assert_not_wrapped(anthropic.resources.beta.messages.messages.Messages.create)
            self.assert_not_wrapped(anthropic.resources.beta.messages.messages.AsyncMessages.create)

    def assert_not_module_double_patched(self, anthropic):
        self.assert_not_double_wrapped(anthropic.resources.messages.Messages.create)
        self.assert_not_double_wrapped(anthropic.resources.messages.AsyncMessages.create)
        if ANTHROPIC_VERSION >= (0, 37):
            self.assert_not_double_wrapped(anthropic.resources.beta.messages.messages.Messages.create)
            self.assert_not_double_wrapped(anthropic.resources.beta.messages.messages.AsyncMessages.create)
