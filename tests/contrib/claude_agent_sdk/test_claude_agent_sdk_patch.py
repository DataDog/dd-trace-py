from ddtrace.contrib.internal.claude_agent_sdk.patch import get_version
from ddtrace.contrib.internal.claude_agent_sdk.patch import patch


try:
    from ddtrace.contrib.internal.claude_agent_sdk.patch import unpatch
except ImportError:
    unpatch = None

from tests.contrib.patch import PatchTestCase


class TestClaudeAgentSdkPatch(PatchTestCase.Base):
    __integration_name__ = "claude_agent_sdk"
    __module_name__ = "claude_agent_sdk"
    __patch_func__ = patch
    __unpatch_func__ = unpatch
    __get_version__ = get_version

    def assert_module_patched(self, claude_agent_sdk):
        self.assert_wrapped(claude_agent_sdk.query)
        self.assert_wrapped(claude_agent_sdk.ClaudeSDKClient.query)
        self.assert_wrapped(claude_agent_sdk.ClaudeSDKClient.receive_messages)

    def assert_not_module_patched(self, claude_agent_sdk):
        self.assert_not_wrapped(claude_agent_sdk.query)
        self.assert_not_wrapped(claude_agent_sdk.ClaudeSDKClient.query)
        self.assert_not_wrapped(claude_agent_sdk.ClaudeSDKClient.receive_messages)

    def assert_not_module_double_patched(self, claude_agent_sdk):
        self.assert_not_double_wrapped(claude_agent_sdk.query)
        self.assert_not_double_wrapped(claude_agent_sdk.ClaudeSDKClient.query)
        self.assert_not_double_wrapped(claude_agent_sdk.ClaudeSDKClient.receive_messages)
